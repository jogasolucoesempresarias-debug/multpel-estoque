'use strict';
/* Painel de Estoque Multpel v2 — foco no comprador. Baixa snapshot 1x e deriva tudo client-side. */

Chart.defaults.color = '#94a3b8';
Chart.defaults.font.family = 'DM Sans, sans-serif';
Chart.defaults.borderColor = '#1e293b';

const C = { green:'#34d399', red:'#f87171', orange:'#fb923c', yellow:'#fbbf24',
            accent:'#38bdf8', accent2:'#818cf8', purple:'#c084fc', dim:'#64748b' };
const FAIXAS = [['0-30',0,30],['31-60',31,60],['61-90',61,90],['91-120',91,120],['121+',121,Infinity]];
const PREF = 'multpel_estoque_prefs';

const S = {
  meta:null, produtosAll:[], validade:null, planos:{}, orcamento:null, view:'cockpit',
  filiaisAll:[], filiaisSel:new Set(), base:'endereco', vperiodo:'mes', cvDim:'comprador',
  compradorNome:'',
  cli:{comprador:'',curva:'',xyz:'',fornec:'',depto:'',busca:'',abast:'',parado:'',ruptura:''},
  params:{lead:10,seg:25,cob:45,hor:30,parado:60,forecast:0,sazonal:0,fcmeses:6,arredondacx:1},
  charts:{}, sort:{}, paradoMin:0,
};

/* ───────── helpers ───────── */
const $ = s => document.querySelector(s);
const moneyF = new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'});
const money = v => v==null ? '—' : moneyF.format(v);
const moneyK = v => v==null ? '—' : (Math.abs(v)>=1000 ? 'R$ '+new Intl.NumberFormat('pt-BR',{maximumFractionDigits:1}).format(v/1000)+'k' : moneyF.format(v));
const int = v => v==null ? '—' : new Intl.NumberFormat('pt-BR',{maximumFractionDigits:0}).format(v);
const dec = (v,d=1) => v==null ? '—' : new Intl.NumberFormat('pt-BR',{maximumFractionDigits:d}).format(v);
const pct = v => v==null ? '—' : new Intl.NumberFormat('pt-BR',{style:'percent',maximumFractionDigits:1}).format(v);
const cob = v => v==null ? '∞' : dec(v,0)+'d';
// sugestão em caixas fechadas quando há QTUNITCX>1: "4 cx · 48 un"
const sugCx = (un, qtcx) => un==null ? '—' : ((qtcx>1 && un>0) ? `${int(Math.ceil(un/qtcx))} cx · ${int(un)} un` : int(un));
const dt = s => s ? s.split('-').reverse().join('/') : '—';
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const badge = (v,txt) => v==null||v===''?'':`<span class="badge b-${String(v).replace(/[^a-z0-9_-]/gi,'')}">${esc(txt!=null?txt:v)}</span>`;
function spark(serie){ // mini sparkline SVG de 3 meses
  if(!serie||!serie.length) return '';
  const mx=Math.max(...serie,1), w=46,h=16, st=w/(serie.length-1||1);
  const pts=serie.map((v,i)=>`${(i*st).toFixed(1)},${(h-(v/mx)*(h-2)-1).toFixed(1)}`).join(' ');
  const up=serie[serie.length-1]>=serie[0];
  return `<svg width="${w}" height="${h}" class="spark"><polyline points="${pts}" fill="none" stroke="${up?C.green:C.red}" stroke-width="1.5"/></svg>`;
}
function toast(msg,err){ const t=document.createElement('div'); t.className='toast'+(err?'':' ok'); t.textContent=msg; document.body.appendChild(t); setTimeout(()=>t.remove(),3500); }
async function getJSON(u){ const r=await fetch(u); if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }
async function postJSON(u,body,method){ const r=await fetch(u,{method:method||'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); if(!r.ok) throw new Error('HTTP '+r.status); return r.json(); }

/* ───────── prefs ───────── */
function savePrefs(){ try{ localStorage.setItem(PREF, JSON.stringify({comprador:S.cli.comprador,base:S.base,vperiodo:S.vperiodo,filiais:[...S.filiaisSel],params:S.params,view:S.view})); }catch(e){} }
function loadPrefs(){ try{ return JSON.parse(localStorage.getItem(PREF))||{}; }catch(e){ return {}; } }

/* ───────── querystring p/ servidor ───────── */
function serverQS(){
  const p=new URLSearchParams(), sel=[...S.filiaisSel];
  if(sel.length && sel.length<S.filiaisAll.length) p.set('filiais', sel.join(','));
  p.set('base_estoque', S.base);
  p.set('venda_periodo', S.vperiodo);
  p.set('lead_time', S.params.lead); p.set('dias_seguranca', S.params.seg);
  p.set('cobertura_total', S.params.cob); p.set('horizonte_val', S.params.hor);
  p.set('parado_atencao', S.params.parado);
  p.set('forecast', S.params.forecast?1:0); p.set('forecast_meses', S.params.fcmeses);
  p.set('forecast_sazonal', S.params.sazonal?1:0); p.set('arredonda_cx', S.params.arredondacx?1:0);
  return p.toString();
}

/* ───────── carga ───────── */
async function loadData(){
  $('#loader').style.display='block'; $('#content').style.display='none';
  try{
    const qs=serverQS();
    const [snap,val,planos]=await Promise.all([
      getJSON('/api/snapshot?'+qs), getJSON('/api/validade?'+qs), getJSON('/api/planos').catch(()=>({planos:{}}))]);
    S.produtosAll=snap.produtos; S.meta=snap; S.validade=val; S.planos=planos.planos||{};
    $('#meta-gerado').textContent='Atualizado em '+snap.gerado_em;
    $('#meta-filiais').textContent='filiais '+(Array.isArray(snap.filiais)?snap.filiais.join(','):snap.filiais)+' · '+snap.n+' itens · '+(S.base==='endereco'?'endereçado':'gerencial');
  }catch(e){ toast('Falha ao carregar: '+e.message,true); console.error(e); }
  $('#loader').style.display='none'; $('#content').style.display='block';
  render();
}

/* ───────── filtros client-side ───────── */
function filtered(){
  const f=S.cli, b=f.busca.trim().toLowerCase();
  return S.produtosAll.filter(p=>{
    if(f.comprador && String(p.codcomprador)!==f.comprador) return false;
    if(f.curva && p.curva_abc!==f.curva) return false;
    if(f.xyz && p.xyz!==f.xyz) return false;
    if(f.fornec && String(p.codfornec)!==f.fornec) return false;
    if(f.depto && String(p.codepto)!==f.depto) return false;
    if(f.abast && p.status_abast!==f.abast) return false;
    if(f.parado && p.status_parado!==f.parado) return false;
    if(f.ruptura && !p.status_ruptura) return false;
    if(b && !(String(p.codprod).includes(b)||(p.descricao||'').toLowerCase().includes(b))) return false;
    return true;
  });
}
function lotesFiltrados(){
  const f=S.cli, L=S.validade?.lotes||[];
  if(!f.comprador) return L;
  const cods=new Set(S.produtosAll.filter(p=>String(p.codcomprador)===f.comprador).map(p=>p.codprod));
  return L.filter(l=>cods.has(l.codprod));
}

/* ───────── agregação cockpit ───────── */
function agg(P){
  const sum=(a,fn)=>a.reduce((s,p)=>s+(fn(p)||0),0);
  const valor_total=sum(P,p=>p.valor);
  const comGiro=P.filter(p=>(p.giro_dia||0)>0);
  const semGiro=P.filter(p=>(p.giro_dia||0)<=0&&(p.qtdisp||0)>0);
  const parados=P.filter(p=>p.status_parado);
  const repor=P.filter(p=>(p.sugestao_compra||0)>0&&(p.giro_dia||0)>0&&!p.compra_suspensa);
  const rupt=P.filter(p=>p.status_ruptura);
  const faixas=FAIXAS.map(([n,lo,hi])=>{const it=comGiro.filter(p=>p.cobertura!=null&&p.cobertura>=lo&&p.cobertura<=hi);return{faixa:n,qt:it.length,valor:sum(it,p=>p.valor)};});
  faixas.push({faixa:'sem giro',qt:semGiro.length,valor:sum(semGiro,p=>p.valor)});
  const abc={}; ['A','B','C'].forEach(c=>{const it=P.filter(p=>p.curva_abc===c);abc[c]={qt:it.length,valor:sum(it,p=>p.valor)};});
  const matriz={}; P.forEach(p=>{if(p.abc_xyz){(matriz[p.abc_xyz]=matriz[p.abc_xyz]||{qt:0,valor:0});matriz[p.abc_xyz].qt++;matriz[p.abc_xyz].valor+=(p.valor||0);}});
  const cnt=(fld,v)=>{const it=P.filter(p=>p[fld]===v);return{qt:it.length,valor:sum(it,p=>p.valor)};};
  const venda_total=sum(P,p=>p.venda), lucro_total=sum(P,p=>p.lucro);
  return {valor_total,venda_total,lucro_total,margem_total: venda_total?lucro_total/venda_total*100:null,
    n:P.length,com_estoque:P.filter(p=>(p.qtdisp||0)>0).length,com_giro:comGiro.length,sem_giro:semGiro.length,
    valor_parado:sum(parados,p=>p.valor),valor_sem_giro:sum(semGiro,p=>p.valor),faixas,abc,matriz,
    parado:{atencao:cnt('status_parado','atencao'),critico:cnt('status_parado','critico'),muito_critico:cnt('status_parado','muito_critico')},
    ruptura:{total:rupt.length,valor:sum(rupt,p=>p.valor),f0_15:rupt.filter(p=>p.status_ruptura==='0-15').length},
    repor:{n:repor.length,valor:sum(repor,p=>(p.sugestao_compra||0)*(p.custo_unit||0)),qt:sum(repor,p=>p.sugestao_compra)}};
}

/* ───────── charts / tabela ───────── */
function chart(id,cfg){ if(S.charts[id]) S.charts[id].destroy(); const c=document.getElementById(id); if(c) S.charts[id]=new Chart(c,cfg); }
function renderTable(P,cols,view,onClickRow){
  const sk=S.sort[view]||{key:cols[0].key,dir:-1};
  const rows=[...P].sort((a,b)=>{let x=a[sk.key],y=b[sk.key]; if(x==null)x=-Infinity; if(y==null)y=-Infinity;
    if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y)); return sk.dir*(x-y);});
  const head=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.key}">${c.label}${sk.key===c.key?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('');
  const body=rows.slice(0,400).map(p=>`<tr data-cod="${p.codprod}">`+cols.map(c=>{
    let v=p[c.key]; if(c.badge)return`<td>${badge(v,c.map?c.map(v,p):v)}</td>`;
    if(c.html)return`<td class="${c.num?'num':''}">${c.html(p)}</td>`;
    if(c.fmt)v=c.fmt(v,p); return`<td class="${c.num?'num':''}">${v==null?'—':v}</td>`;}).join('')+'</tr>').join('');
  const note=`<div class="count-line">${int(rows.length)} itens${rows.length>400?' (mostrando 400)':''}</div>`;
  setTimeout(()=>{ const cont=$('#v-'+view);
    cont.querySelectorAll('thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[view]||{};S.sort[view]={key:k,dir:cur.key===k?-cur.dir:-1};render();});
    cont.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=e=>{ if(e.target.closest('.rowact'))return; (onClickRow||openProduto)(tr.dataset.cod);});
  },0);
  return note+`<div class="tbl-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
const colCod={key:'codprod',label:'Cód',num:true};
const colProd={key:'descricao',label:'Produto',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v)}</span>`};
const colForn={key:'fornecedor',label:'Fornecedor',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v||'—')}</span>`};
const colGiroSpark={key:'giro_mes',label:'Giro/mês',num:true,html:p=>`${int(p.giro_mes)} ${spark(p.serie_giro)}`};

function exportBtns(view){ const qs=serverQS(); return `<span class="exp"><a class="btn sm" href="/api/export/${view}.xlsx?${qs}">⬇ Excel</a><a class="btn sm" href="/api/export/${view}.csv?${qs}">CSV</a></span>`; }
function head(title,view){ return `<h2 class="section"><span>${title}</span>${view?exportBtns(view):''}</h2>`; }

/* ───────── VIEWS ───────── */
function kpi(l,v,sub,dot){ return `<div class="card kpi"><div class="k-label">${dot?`<span class="dot" style="background:${dot}"></span>`:''}${l}</div><div class="k-value">${v}</div>${sub?`<div class="k-sub">${sub}</div>`:''}</div>`; }
function alertCard(qt,label,valor,color,view,filt){ return `<div class="alert" style="--c:${color}" data-view="${view}" data-filt='${esc(JSON.stringify(filt||{}))}'><div class="a-top"><div class="a-qt">${int(qt)}</div><div class="a-valor">${moneyK(valor)}</div></div><div class="a-label">${label}</div><div class="a-go">ver →</div></div>`; }
function wireAlerts(el){ el.querySelectorAll('.alert').forEach(a=>a.onclick=()=>goView(a.dataset.view,JSON.parse(a.dataset.filt||'{}'))); }

// cores por SEMÂNTICA de cobertura: ruptura(vermelho) → saudável(verde) → excesso(roxo)
const COR_FAIXA={'0-30':C.red,'31-60':C.green,'61-90':'#22c55e','91-120':C.yellow,'121+':C.purple,'sem giro':C.dim};
function renderCockpit(P){
  const k=agg(P), v=S.validade?.resumo||{};
  const el=$('#v-cockpit');
  const totItens=P.length||1;
  const periodoLbl={mes:'no mês','90d':'90 dias','6m':'6 meses','12m':'12 meses'}[S.vperiodo];
  el.innerHTML=`
   <div class="kpi-grid">
     ${kpi('Valor em estoque',money(k.valor_total),int(k.com_estoque)+' itens (compras)',C.accent)}
     ${kpi('Venda '+periodoLbl,money(k.venda_total),'lucro '+moneyK(k.lucro_total),C.green)}
     ${kpi('Margem',k.margem_total!=null?dec(k.margem_total,1)+'%':'—','venda × custo',C.accent2)}
     ${kpi('Em ruptura',int(k.ruptura.total),moneyK(k.ruptura.valor),C.red)}
     ${kpi('A comprar',int(k.repor.n),'sug. '+moneyK(k.repor.valor),C.orange)}
     ${kpi('Capital parado',moneyK(k.valor_parado),dec(k.valor_total?k.valor_parado/k.valor_total*100:0,1)+'% do estoque',C.purple)}
   </div>
   <h2 class="section"><span>Alertas de ação</span></h2>
   <div class="alerts">
     ${alertCard(k.ruptura.f0_15,'Ruptura crítica (≤15d)',k.ruptura.valor,C.red,'ruptura',{ruptura:'1'})}
     ${alertCard(k.repor.n,'Comprar (cobertura baixa)',k.repor.valor,C.orange,'reposicao',{})}
     ${alertCard(v.critico||0,'Vencimento ≤7 dias',v.valor_risco,C.yellow,'validade',{})}
     ${alertCard(k.parado.muito_critico.qt,'Parado 120+ dias',k.parado.muito_critico.valor,C.purple,'parado',{parado:'muito_critico'})}
   </div>
   <div class="row">
     <div class="panel grow"><h3>Onde está o capital (cobertura)</h3>
       <div class="row" style="align-items:center"><div style="width:200px"><div class="chart-box sm" style="height:190px"><canvas id="ch-faixas"></canvas></div></div>
       <div class="grow"><table class="mini">${k.faixas.map(f=>`<tr><td><span class="dot" style="background:${COR_FAIXA[f.faixa]}"></span> ${f.faixa}${f.faixa!=='sem giro'?'d':''}</td><td class="num">${int(f.qt)}</td><td class="num">${money(f.valor)}</td></tr>`).join('')}</table></div></div>
     </div>
     <div class="panel grow"><h3>Curva ABC (valor)</h3><div class="chart-box sm" style="height:190px"><canvas id="ch-abc"></canvas></div>
       <table class="mini" style="margin-top:10px">${['A','B','C'].map(c=>`<tr><td>Curva ${c}</td><td class="num">${dec(k.abc[c].qt/totItens*100,0)}% dos itens</td><td class="num">${dec(k.valor_total?k.abc[c].valor/k.valor_total*100:0,0)}% do valor</td></tr>`).join('')}</table>
     </div>
   </div>
   <div class="row">
     <div class="panel grow"><h3>Maiores ofensores — capital parado</h3><div id="cp-parado"></div></div>
     <div class="panel grow"><h3>Maiores ofensores — risco de vencimento</h3><div id="cp-venc"></div></div>
   </div>`;
  chart('ch-faixas',{type:'doughnut',data:{labels:k.faixas.map(f=>f.faixa),datasets:[{data:k.faixas.map(f=>f.valor),backgroundColor:k.faixas.map(f=>COR_FAIXA[f.faixa]),borderWidth:0}]},options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>k.faixas[c.dataIndex].faixa+': '+money(c.raw)}}},cutout:'62%'}});
  chart('ch-abc',{type:'bar',data:{labels:['A','B','C'],datasets:[{data:['A','B','C'].map(c=>k.abc[c].valor),backgroundColor:[C.green,C.accent,C.dim],borderRadius:6}]},options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+' · '+k.abc[['A','B','C'][c.dataIndex]].qt+' itens'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
  const topPar=P.filter(p=>p.status_parado).sort((a,b)=>b.valor-a.valor).slice(0,6);
  const topVen=(S.validade?.lotes||[]).slice().sort((a,b)=>b.valor_risco-a.valor_risco).slice(0,6);
  $('#cp-parado').innerHTML=topPar.map(p=>`<div class="lote-row" data-cod="${p.codprod}" style="cursor:pointer"><span class="prod">${esc(p.descricao)}</span><span class="lr-r">${money(p.valor)}<br><small class="muted">${p.dias_sem_venda==null?'sem saída':p.dias_sem_venda+'d s/ venda'}</small></span></div>`).join('')||'<div class="empty">Nada parado 🎉</div>';
  $('#cp-venc').innerHTML=topVen.map(l=>`<div class="lote-row" data-cod="${l.codprod}" style="cursor:pointer"><span class="prod">${esc(l.descricao)}</span><span class="lr-r">${money(l.valor_risco)}<br><small class="muted">vence ${l.dias_para_vencer}d</small></span></div>`).join('')||'<div class="empty">Sem risco no horizonte 🎉</div>';
  el.querySelectorAll('.lote-row[data-cod]').forEach(r=>r.onclick=()=>openProduto(r.dataset.cod));
  wireAlerts(el);
}

function renderFila(P){
  const itens=[]; const f=S.cli;
  P.filter(p=>p.status_ruptura||p.status_abast==='urgente').forEach(p=>itens.push({
    cod:p.codprod,desc:p.descricao,forn:p.fornecedor,motivo:p.estoque_zero?'Estoque zerado':'Cobertura '+cob(p.cobertura),
    tipo:'comprar',prio:p.estoque_zero?0:(p.cobertura||0),valor:(p.sugestao_compra||0)*(p.custo_unit||0),p}));
  lotesFiltrados().filter(l=>l.classificacao!=='planejar').forEach(l=>itens.push({
    cod:l.codprod,desc:l.descricao,forn:l.fornecedor,motivo:'Vence em '+l.dias_para_vencer+'d',
    tipo:'vencimento',prio:100+l.dias_para_vencer,valor:l.valor_risco,lote:l}));
  P.filter(p=>p.status_parado==='muito_critico').forEach(p=>itens.push({
    cod:p.codprod,desc:p.descricao,forn:p.fornecedor,motivo:(p.dias_sem_venda==null?'Sem saída':p.dias_sem_venda+'d s/ venda'),
    tipo:'liquidar',prio:1000,valor:p.valor,p}));
  itens.sort((a,b)=>a.prio-b.prio||b.valor-a.valor);
  const tipoBadge={comprar:['Comprar',C.orange],vencimento:['Vencimento',C.yellow],liquidar:['Liquidar',C.purple]};
  const el=$('#v-fila');
  const quem=f.comprador?(S.compradorNome||'você'):'a empresa';
  el.innerHTML=`<h2 class="section"><span>⚡ Minha Fila — ${itens.length} ações para ${esc(quem)}</span></h2>
    <div class="count-line">Prioridade: ruptura → vencimento → estoque parado crítico. Clique numa ação para resolver.</div>
    ${itens.length?'':'<div class="empty">Nada urgente agora 🎉</div>'}
    <div class="fila">`+itens.slice(0,80).map((it,i)=>`
      <div class="fila-item" data-cod="${it.cod}">
        <div class="fi-tipo" style="--c:${tipoBadge[it.tipo][1]}">${tipoBadge[it.tipo][0]}</div>
        <div class="fi-main"><div class="fi-desc">${esc(it.desc)}</div><div class="fi-sub">${esc(it.forn||'')} · ${it.motivo}</div></div>
        <div class="fi-val">${it.valor?moneyK(it.valor):''}</div>
        <div class="fi-acts rowact">
          ${it.tipo==='comprar'?`<button class="btn sm" data-act="pedido" data-i="${i}">Registrar pedido</button>`:''}
          ${it.tipo==='vencimento'?`<button class="btn sm" data-act="plano" data-i="${i}">Plano de ação</button>`:''}
          ${it.tipo==='liquidar'?`<button class="btn sm" data-act="plano" data-i="${i}">Plano de ação</button>`:''}
          <button class="btn sm" data-act="360" data-i="${i}">360°</button>
        </div>
      </div>`).join('')+`</div>`;
  el.querySelectorAll('.fila-item').forEach(r=>r.onclick=e=>{ if(!e.target.closest('.rowact'))openProduto(r.dataset.cod); });
  el.querySelectorAll('[data-act]').forEach(b=>b.onclick=()=>{ const it=itens[+b.dataset.i], act=b.dataset.act;
    if(act==='360')openProduto(it.cod); else if(act==='pedido')modalPedido(it.p); else if(act==='plano')modalPlano(it); });
}

function renderRuptura(P){
  const rup=P.filter(p=>p.status_ruptura).sort((a,b)=>(a.cobertura||0)-(b.cobertura||0));
  const cols=[colCod,colProd,colForn,{key:'codcomprador',label:'Comprador',fmt:(v,p)=>esc((p.comprador||'').split(' ')[0]||'—')},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},{key:'cobertura',label:'Cob.',num:true,fmt:cob},
    colGiroSpark,{key:'sugestao_compra',label:'Sugerido',num:true,fmt:int},
    {key:'status_ruptura',label:'Faixa',badge:true,map:v=>v+'d'}];
  $('#v-ruptura').innerHTML=head('Ruptura — cobertura ≤ 30 dias','ruptura')+
    `<div class="count-line">Item em ruptura quando a cobertura (estoque ÷ giro diário) fica ≤ 30 dias.</div>`+renderTable(rup,cols,'ruptura');
}

function renderReposicao(P){
  const rep=P.filter(p=>(p.sugestao_compra||0)>0&&(p.giro_dia||0)>0&&!p.compra_suspensa);
  const suspensos=P.filter(p=>p.compra_suspensa).sort((a,b)=>(b.sugestao_compra*b.custo_unit)-(a.sugestao_compra*a.custo_unit));
  // agrupa por fornecedor
  const g={}; rep.forEach(p=>{(g[p.codfornec]=g[p.codfornec]||{cod:p.codfornec,forn:p.fornecedor||('Forn '+p.codfornec),itens:[],valor:0}); g[p.codfornec].itens.push(p); g[p.codfornec].valor+=(p.sugestao_compra||0)*(p.custo_unit||0);});
  const grupos=Object.values(g).sort((a,b)=>b.valor-a.valor);
  const el=$('#v-reposicao');
  el.innerHTML=head('Reposição — o que comprar (por fornecedor)','reposicao')+
    `<div class="count-line">Sugestão = estoque-alvo (giro/dia × cobertura alvo) − (disponível + trânsito + pendente). Lead time usa o prazo do fornecedor quando houver.</div>`+
    grupos.slice(0,40).map(gr=>`
      <div class="panel forn-grp">
        <h3><span>${esc(gr.forn)} <small class="muted">· ${gr.itens.length} itens</small></span>
          <span>${money(gr.valor)} <button class="btn sm primary rowact" data-fornped="${gr.cod}">Gerar pedido</button></span></h3>
        <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th class="num">Disp.</th><th class="num">Cob.</th><th class="num">Giro/mês</th><th class="num">Sugerido</th><th class="num">Valor</th></tr></thead>
        <tbody>${gr.itens.sort((a,b)=>(a.cobertura||0)-(b.cobertura||0)).map(p=>`<tr data-cod="${p.codprod}"><td class="num">${p.codprod}</td><td><span class="prod">${esc(p.descricao)}</span></td><td class="num">${int(p.qtdisp)}</td><td class="num">${cob(p.cobertura)}</td><td class="num">${int(p.giro_mes)}</td><td class="num">${sugCx(p.sugestao_compra,p.qtunitcx)}</td><td class="num">${money((p.sugestao_compra||0)*(p.custo_unit||0))}</td></tr>`).join('')}</tbody></table></div>
      </div>`).join('')+
    (suspensos.length?`<div class="panel" style="border-color:var(--orange)">
      <h3><span>⚠ Rever antes de comprar — pararam de vender (${suspensos.length})</span></h3>
      <div class="count-line">Têm giro na média de 3 meses, mas <b>sem venda há ≥60 dias</b> → a sugestão pode estar comprando estoque que travou. Confira antes de pedir.</div>
      <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Fornecedor</th><th class="num">Disp.</th><th class="num">Dias s/ venda</th><th class="num">Giro/mês</th><th class="num">Sugeria</th></tr></thead>
      <tbody>${suspensos.slice(0,100).map(p=>`<tr data-cod="${p.codprod}"><td class="num">${p.codprod}</td><td><span class="prod">${esc(p.descricao)}</span></td><td><span class="prod">${esc(p.fornecedor||'—')}</span></td><td class="num">${int(p.qtdisp)}</td><td class="num">${p.dias_sem_venda==null?'—':int(p.dias_sem_venda)}</td><td class="num">${int(p.giro_mes)}</td><td class="num">${int(p.sugestao_compra)}</td></tr>`).join('')}</tbody></table></div>
    </div>`:'');
  el.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=e=>{if(!e.target.closest('.rowact'))openProduto(tr.dataset.cod);});
  el.querySelectorAll('[data-fornped]').forEach(b=>b.onclick=()=>{ const gr=grupos.find(x=>String(x.cod)===b.dataset.fornped); modalPedidoFornecedor(gr); });
}

async function renderPlano(){
  const el=$('#v-plano');
  el.innerHTML=`<div class="loader"><div class="spinner"></div>Calculando plano de reposição…</div>`;
  let j; try{ j=await getJSON('/api/plano_reposicao?'+serverQS()); }
  catch(e){ el.innerHTML=`<div class="empty">Falha ao montar o plano: ${esc(e.message)}</div>`; return; }
  // aplica TODOS os filtros client (fornecedor, comprador, curva, XYZ, depto, busca) via filtered()
  const allow=new Set(filtered().map(p=>p.codprod));
  let itens=(j.itens||[]).filter(p=>allow.has(p.codprod));
  // explode liberações em buckets por semana
  const buckets={};
  itens.forEach(p=>p.liberacoes.forEach(l=>{(buckets[l.semana]=buckets[l.semana]||[]).push({...p,...l});}));
  const semanas=Object.keys(buckets).map(Number).sort((a,b)=>a-b);
  const fonteLbl=S.params.sazonal?'forecast sazonal (RCA, 24m)':(S.params.forecast?`forecast (RCA, ${S.params.fcmeses}m)`:'média 3m (oficial)');
  const totAgora=(buckets[0]||[]).reduce((s,x)=>s+(x.valor||0),0);
  const totFuturo=semanas.filter(w=>w>0).reduce((s,w)=>s+buckets[w].reduce((a,x)=>a+(x.valor||0),0),0);
  let html=`<h2 class="section"><span>Plano de reposição no tempo</span></h2>
    <div class="count-line">Saldo projetado semana a semana (demanda = giro/dia · ${fonteLbl}). Mostra <b>quando o pedido precisa sair</b> = recebimento − lead time. Sem dados de trânsito no BI → todo reabastecimento é planejado.</div>
    <div class="kpi-grid" style="grid-template-columns:repeat(3,1fr)">
      ${kpi('Liberar agora (esta semana)',money(totAgora),int((buckets[0]||[]).length)+' itens',C.orange)}
      ${kpi('Liberações futuras (12 sem.)',money(totFuturo),int(semanas.filter(w=>w>0).reduce((s,w)=>s+buckets[w].length,0))+' itens',C.accent)}
      ${kpi('Itens no plano',int(itens.length),'com giro e sugestão',C.accent2)}
    </div>`;
  if(!semanas.length){ el.innerHTML=html+'<div class="empty">Nenhuma reposição necessária no horizonte 🎉</div>'; return; }
  const hoje=new Date();
  html+=semanas.map(w=>{
    const lib=buckets[w].sort((a,b)=>b.valor-a.valor);
    const tot=lib.reduce((s,x)=>s+(x.valor||0),0);
    const dataLbl=new Date(hoje.getTime()+w*7*864e5).toLocaleDateString('pt-BR');
    const titulo=w===0?'⚡ Sair agora (esta semana)':`Semana +${w} · a partir de ${dataLbl}`;
    return `<div class="panel forn-grp">
      <h3><span>${titulo} <small class="muted">· ${lib.length} itens</small></span><span>${money(tot)}</span></h3>
      <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Fornecedor</th><th class="num">Disp.</th><th class="num">Cob.</th><th class="num">Giro/mês</th><th class="num">Qtd pedir</th><th class="num">Valor</th></tr></thead>
      <tbody>${lib.map(x=>`<tr data-cod="${x.codprod}"><td class="num">${x.codprod}</td><td><span class="prod">${esc(x.descricao)}</span></td><td><span class="prod">${esc(x.fornecedor||'—')}</span></td><td class="num">${int(x.qtdisp)}</td><td class="num">${cob(x.cobertura)}</td><td class="num">${int(x.giro_mes)}</td><td class="num">${sugCx(x.qt,x.qtunitcx)}</td><td class="num">${money(x.valor)}</td></tr>`).join('')}</tbody></table></div>
    </div>`;
  }).join('');
  el.innerHTML=html;
  el.querySelectorAll('tbody tr[data-cod]').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
}

function renderValidade(){
  const L=lotesFiltrados();
  const cols=[colCod,{key:'descricao',label:'Produto',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v)}</span>`},
    {key:'numlote',label:'Lote'},{key:'dtval',label:'Validade',fmt:dt},{key:'dias_para_vencer',label:'Dias',num:true},
    {key:'qt',label:'Qtd',num:true,fmt:int},{key:'saldo_proj',label:'Saldo proj.',num:true,fmt:int},
    {key:'valor_risco',label:'Valor risco',num:true,fmt:money},{key:'classificacao',label:'Classe',badge:true},
    {key:'_plano',label:'Ação',html:l=>planoCell('validade',l.codprod+'|'+l.dtval,l.codprod,l.descricao,l.dtval)}];
  // faixas
  const faixas=[['0-15',0,15],['16-30',16,30],['31-60',31,60],['61-90',61,90],['90+',91,1e9]];
  const fd=faixas.map(([n,lo,hi])=>{const it=L.filter(l=>l.dias_para_vencer>=lo&&l.dias_para_vencer<=hi);return{n,qt:it.length,valor:it.reduce((s,l)=>s+(l.valor_risco||0),0)};});
  const el=$('#v-validade');
  el.innerHTML=head(`Validade / FEFO — próximos ${S.params.hor} dias`,'validade')+
    `<div class="row"><div class="panel grow" style="max-width:420px"><h3>Risco por faixa de dias</h3><div class="chart-box sm"><canvas id="ch-val"></canvas></div></div>
     <div class="panel grow" id="val-tbl"></div></div>`;
  $('#val-tbl').innerHTML=renderTableInline(L,cols,'validade');
  chart('ch-val',{type:'bar',data:{labels:fd.map(f=>f.n),datasets:[{data:fd.map(f=>f.valor),backgroundColor:[C.red,C.orange,C.yellow,C.accent,C.dim],borderRadius:6}]},options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+' · '+fd[c.dataIndex].qt+' lotes'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
  wirePlanoCells();
}
function renderTableInline(P,cols,view){ // tabela sem o wrapper de section (usada dentro de painel)
  const sk=S.sort[view]||{key:'dias_para_vencer',dir:1};
  const rows=[...P].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=Infinity;if(y==null)y=Infinity;if(typeof x==='string')return sk.dir*String(x).localeCompare(String(y));return sk.dir*(x-y);});
  const headr=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.key}">${c.label}${sk.key===c.key?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('');
  const body=rows.slice(0,300).map(p=>`<tr data-cod="${p.codprod}">`+cols.map(c=>{let v=p[c.key];if(c.badge)return`<td>${badge(v,c.map?c.map(v):v)}</td>`;if(c.html)return`<td>${c.html(p)}</td>`;if(c.fmt)v=c.fmt(v,p);return`<td class="${c.num?'num':''}">${v==null?'—':v}</td>`;}).join('')+'</tr>').join('');
  setTimeout(()=>{const cont=$('#val-tbl');if(!cont)return;
    cont.querySelectorAll('thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[view]||{};S.sort[view]={key:k,dir:cur.key===k?-cur.dir:-1};render();});
    cont.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=e=>{if(!e.target.closest('.rowact'))openProduto(tr.dataset.cod);});
  },0);
  return `<div class="count-line">${int(rows.length)} lotes</div><div class="tbl-wrap"><table><thead><tr>${headr}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderParado(P){
  const allPar=P.filter(p=>p.status_parado);
  const cols=[colCod,colProd,colForn,{key:'dtultsaida',label:'Última venda',fmt:v=>dt(v)},
    {key:'dias_sem_venda',label:'Dias s/ venda',num:true,fmt:v=>v==null?'sem saída':int(v)},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},{key:'valor',label:'Valor',num:true,fmt:money},
    {key:'status_saida',label:'Saída',badge:true},{key:'status_parado',label:'Classe',badge:true},
    {key:'_plano',label:'Ação',html:p=>planoCell('parado',String(p.codprod),p.codprod,p.descricao,null)}];
  // filtro rápido (client-side, instantâneo): sem venda ≥ X dias — "sem saída" sempre incluído
  $('#v-parado').innerHTML=head('Estoque parado — o que liquidar','parado')
    +`<div class="count-line">Filtro rápido — sem venda ≥ <input type="number" id="pq-parado" value="${S.paradoMin||''}" placeholder="0" style="width:72px"> dias</div>`
    +`<div id="parado-tbl"></div>`;
  const desenha=()=>{ const min=+($('#pq-parado').value)||0;
    const par=allPar.filter(p=>p.dias_sem_venda==null||p.dias_sem_venda>=min).sort((a,b)=>b.valor-a.valor);
    $('#parado-tbl').innerHTML=renderTable(par,cols,'parado'); wirePlanoCells(); };
  $('#pq-parado').oninput=()=>{ S.paradoMin=+$('#pq-parado').value||0; desenha(); };
  desenha();
}

function renderABCXYZ(P){
  const m={}; P.forEach(p=>{if(p.abc_xyz){(m[p.abc_xyz]=m[p.abc_xyz]||{qt:0,valor:0});m[p.abc_xyz].qt++;m[p.abc_xyz].valor+=(p.valor||0);}});
  const grid=`<div class="matrix"><div></div><div class="mh">X · estável</div><div class="mh">Y · variável</div><div class="mh">Z · errático</div>`+
    ['A','B','C'].map(a=>`<div class="mh">${a}</div>`+['X','Y','Z'].map(x=>{const k=a+x,d=m[k];return d&&d.qt?`<div class="mcell" data-key="${k}"><div class="mc-key">${k}</div><div class="mc-qt">${int(d.qt)}</div><div class="mc-val">${moneyK(d.valor)}</div></div>`:`<div class="mcell empty"><div class="mc-key">${k}</div><div class="mc-qt">0</div></div>`;}).join('')).join('')+`</div>`;
  $('#v-abcxyz').innerHTML=`<h2 class="section"><span>Matriz ABC-XYZ</span></h2>
    <div class="row"><div class="panel"><h3>Valor (ABC) × Variabilidade da demanda (XYZ)</h3>${grid}<div class="count-line" style="margin-top:14px">AX = controle rígido · CZ = candidatos a descontinuar. Clique para listar em Produtos.</div></div>
    <div class="panel grow"><h3>Estratégia</h3>
     <div class="lote-row"><b>A·X/Y</b><span class="lr-r">Nunca faltar. Reposição automática.</span></div>
     <div class="lote-row"><b>A·Z</b><span class="lr-r">Alto valor, demanda imprevisível. Monitorar.</span></div>
     <div class="lote-row"><b>C·Z</b><span class="lr-r">Candidatos a descontinuar / compra sob demanda.</span></div></div></div>`;
  $('#v-abcxyz').querySelectorAll('.mcell[data-key]').forEach(c=>c.onclick=()=>{const k=c.dataset.key;S.cli.curva=k[0];S.cli.xyz=k[1];$('#f-curva').value=k[0];$('#f-xyz').value=k[1];goView('produtos',{});});
}

function renderFornecedores(P){
  const tv=P.reduce((s,p)=>s+(p.valor||0),0)||1,tg=P.reduce((s,p)=>s+(p.giro_mes||0),0)||1,g={};
  P.forEach(p=>{if(p.codfornec==null)return;const o=g[p.codfornec]=g[p.codfornec]||{codfornec:p.codfornec,fornecedor:p.fornecedor||('FORN '+p.codfornec),n_produtos:0,valor:0,giro:0,venda:0,lucro:0,disp:0,girodia:0};o.n_produtos++;o.valor+=(p.valor||0);o.giro+=(p.giro_mes||0);o.venda+=(p.venda||0);o.lucro+=(p.lucro||0);o.disp+=(p.qtdisp||0);o.girodia+=(p.giro_dia||0);});
  const lead=S.params.lead||10;
  const F=Object.values(g).map(o=>{const pg=o.giro/tg*100,pe=o.valor/tv*100,idx=pe>0?pg/pe:(pg>0?999:0),cobertura=o.girodia>0?o.disp/o.girodia:null;
    let cl=o.giro<=0?'critico_sem_giro':(cobertura!=null&&cobertura<lead?'ruptura':(idx>=1.2?'alta_performance':(idx>=0.8?'equilibrado':'estoque_alto')));
    return{...o,pg,pe,idx,cobertura,margem:o.venda?o.lucro/o.venda*100:null,cl};}).sort((a,b)=>b.valor-a.valor);
  const cols=[{key:'codfornec',label:'Cód',num:true},{key:'fornecedor',label:'Fornecedor',fmt:v=>`<span class="prod">${esc(v)}</span>`},
    {key:'n_produtos',label:'Itens',num:true},{key:'valor',label:'Estoque',num:true,fmt:money},{key:'giro',label:'Giro/mês',num:true,fmt:int},
    {key:'cobertura',label:'Cob.',num:true,fmt:cob},
    {key:'venda',label:'Venda',num:true,fmt:money},{key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
    {key:'pe',label:'% est.',num:true,fmt:v=>dec(v,1)+'%'},{key:'pg',label:'% giro',num:true,fmt:v=>dec(v,1)+'%'},
    {key:'idx',label:'Índice',num:true,fmt:v=>dec(v,2)},{key:'cl',label:'Classe',badge:true}];
  const sk=S.sort['fornecedores']||{key:'valor',dir:-1};
  const rows=[...F].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(typeof x==='string')return sk.dir*x.localeCompare(y);return sk.dir*((x||0)-(y||0));});
  const headr=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.key}">${c.label}</th>`).join('');
  const body=rows.slice(0,300).map(r=>'<tr>'+cols.map(c=>{let v=r[c.key];if(c.badge)return`<td>${badge(v)}</td>`;if(c.fmt)v=c.fmt(v);return`<td class="${c.num?'num':''}">${v==null?'—':v}</td>`;}).join('')+'</tr>').join('');
  $('#v-fornecedores').innerHTML=head('Desempenho por fornecedor — giro × estoque','fornecedores')+
    `<div class="count-line">Índice = % no giro ÷ % no estoque (&gt;1 = gira mais do que pesa). <b>Ruptura</b> = gira mas cobertura &lt; ${lead}d (quase sem estoque) — não é performance.</div><div class="tbl-wrap"><table><thead><tr>${headr}</tr></thead><tbody>${body}</tbody></table></div>`;
  $('#v-fornecedores').querySelectorAll('thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort['fornecedores']||{};S.sort['fornecedores']={key:k,dir:cur.key===k?-cur.dir:-1};render();});
}

function renderProdutos(P){
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'xyz',label:'XYZ',badge:true},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},colGiroSpark,{key:'cobertura',label:'Cob.',num:true,fmt:cob},
    {key:'dias_sem_venda',label:'Dias s/v',num:true,fmt:v=>v==null?'—':int(v)},{key:'valor',label:'Valor',num:true,fmt:money},
    {key:'status_abast',label:'Abast.',badge:true}];
  $('#v-produtos').innerHTML=head('Explorador de produtos','produtos')+renderTable(P,cols,'produtos');
}

function renderComprasVendas(P){
  const dim=S.cvDim, el=$('#v-comprasvendas');
  const seg=`<div class="seg" id="cv-seg">
    ${['comprador','fornecedor','produto'].map(d=>`<span class="seg-opt ${d===dim?'on':''}" data-d="${d}">${({comprador:'Por comprador',fornecedor:'Por fornecedor',produto:'Por produto'})[d]}</span>`).join('')}</div>`;
  const expv=dim==='comprador'?'compradores':(dim==='fornecedor'?'fornecedores':'comprasvendas');
  let html=`<h2 class="section"><span>Compras × Vendas — ${({comprador:'por comprador',fornecedor:'por fornecedor',produto:'por produto'})[dim]}</span>${exportBtns(expv)}</h2>
    <div class="count-line" style="display:flex;justify-content:space-between;align-items:center">${seg}<span>Estoque = capital em compras · Venda/Lucro/Margem = realizado no período (${({mes:'mês',['90d']:'90d',['6m']:'6m',['12m']:'12m'})[S.vperiodo]})</span></div>`;
  if(dim==='produto'){
    const cols=[colCod,colProd,colForn,{key:'comprador',label:'Comprador',fmt:v=>esc((v||'').split(' ')[0]||'—')},
      {key:'valor',label:'Estoque R$',num:true,fmt:money},{key:'venda',label:'Venda R$',num:true,fmt:money},
      {key:'lucro',label:'Lucro R$',num:true,fmt:money},{key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
      colGiroSpark,{key:'cobertura',label:'Cob.',num:true,fmt:cob}];
    html+=renderTable(P,cols,'comprasvendas');
    el.innerHTML=html;
  } else {
    const g={};
    P.forEach(p=>{const key=dim==='fornecedor'?p.codfornec:p.codcomprador; if(key==null)return;
      const nome=dim==='fornecedor'?(p.fornecedor||'Forn '+key):(p.comprador||'Sem comprador');
      const o=g[key]=g[key]||{key,nome,n:0,estoque:0,venda:0,lucro:0,giro:0,rupt:0,parado:0};
      o.n++; o.estoque+=(p.valor||0); o.venda+=(p.venda||0); o.lucro+=(p.lucro||0); o.giro+=(p.giro_mes||0);
      if(p.status_ruptura)o.rupt++; if(p.status_parado)o.parado+=(p.valor||0);});
    const rows=Object.values(g).map(o=>({...o,margem:o.venda?o.lucro/o.venda*100:null,turn:o.estoque?o.venda/o.estoque:null})).sort((a,b)=>b.venda-a.venda);
    const totE=rows.reduce((s,r)=>s+r.estoque,0),totV=rows.reduce((s,r)=>s+r.venda,0),totL=rows.reduce((s,r)=>s+r.lucro,0);
    html+=`<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
      ${kpi('Estoque (compras)',money(totE),'',C.accent)}${kpi('Venda',money(totV),'',C.green)}
      ${kpi('Lucro',money(totL),'',C.accent2)}${kpi('Margem',totV?dec(totL/totV*100,1)+'%':'—','',C.purple)}</div>`;
    html+=`<div class="tbl-wrap"><table><thead><tr><th>${dim==='fornecedor'?'Fornecedor':'Comprador'}</th><th class="num">Itens</th><th class="num">Estoque R$</th><th class="num">Venda R$</th><th class="num">Lucro R$</th><th class="num">Margem</th><th class="num">Venda/Estoque</th><th class="num">Ruptura</th><th class="num">Parado R$</th></tr></thead><tbody>`+
      rows.map(r=>`<tr><td><span class="prod">${esc(r.nome)}</span></td><td class="num">${int(r.n)}</td><td class="num">${money(r.estoque)}</td><td class="num">${money(r.venda)}</td><td class="num">${money(r.lucro)}</td><td class="num">${r.margem==null?'—':dec(r.margem,1)+'%'}</td><td class="num">${r.turn==null?'—':dec(r.turn,2)+'×'}</td><td class="num">${int(r.rupt)}</td><td class="num">${money(r.parado)}</td></tr>`).join('')+
      `</tbody></table></div><div class="count-line">${rows.length} ${dim==='fornecedor'?'fornecedores':'compradores'} · "Venda/Estoque" = quantas vezes o capital girou no período.</div>`;
    el.innerHTML=html;
  }
  el.querySelectorAll('#cv-seg .seg-opt').forEach(o=>o.onclick=()=>{S.cvDim=o.dataset.d;render();});
  el.querySelectorAll('tbody tr[data-cod]').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
}

/* ───────── Orçamento ───────── */
async function renderOrcamento(){
  const el=$('#v-orcamento');
  const comp=S.compradorNome||'TODOS';
  el.innerHTML=`<div class="loader"><div class="spinner"></div></div>`;
  let o; try{ o=await getJSON('/api/orcamento?comprador='+encodeURIComponent(comp)); }
  catch(e){ el.innerHTML=`<div class="empty">Orçamento indisponível (Postgres off): ${e.message}</div>`; return; }
  S.orcamento=o; const r=o.resumo;
  const prog=r.pct!=null?Math.min(100,r.pct*100):0;
  const cor=prog>=100?C.red:(prog>=85?C.orange:C.green);
  el.innerHTML=`<h2 class="section"><span>Orçamento de compras — ${esc(comp)} · ${r.mes}</span>
      <span><button class="btn sm" id="btn-meta">Definir meta</button> <button class="btn sm primary" id="btn-pedido">+ Pedido</button></span></h2>
    <div class="kpi-grid">
      ${kpi('Meta do mês',money(r.meta),'',C.accent)}
      ${kpi('Comprado',money(r.comprado),r.n_pedidos+' pedidos',C.accent2)}
      ${kpi('Saldo',money(r.saldo),'',r.saldo<0?C.red:C.green)}
      ${kpi('Consumido',r.pct!=null?pct(r.pct):'—','',cor)}
    </div>
    <div class="panel"><div class="bar big"><i style="width:${prog}%;background:${cor}"></i></div>
      <div class="count-line">${prog>=100?'⚠️ Meta estourada':(prog>=85?'Atenção: perto da meta':'Dentro do planejado')}</div></div>
    <div class="panel"><h3>Pedidos lançados</h3>
      ${o.pedidos.length?`<div class="tbl-wrap"><table><thead><tr><th>Data</th><th>Fornecedor</th><th>Pedido</th><th class="num">Valor</th><th>Prazo</th><th>Status</th><th></th></tr></thead>
      <tbody>${o.pedidos.map(pe=>`<tr><td>${dt(pe.data_pedido)}</td><td><span class="prod">${esc(pe.fornecedor||'')}</span></td><td>${esc(pe.n_pedido||'')}</td><td class="num">${money(+pe.valor)}</td><td>${pe.prazo_dias||''}${pe.prazo_dias?'d':''}</td><td>${badge((pe.status||'').toLowerCase(),pe.status)}</td><td><button class="btn sm" data-delped="${pe.id}">✕</button></td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Nenhum pedido lançado neste mês.</div>'}
    </div>`;
  $('#btn-meta').onclick=()=>modalMeta(r);
  $('#btn-pedido').onclick=()=>modalPedido(null);
  el.querySelectorAll('[data-delped]').forEach(b=>b.onclick=async()=>{ await postJSON('/api/pedidos/'+b.dataset.delped,{}, 'DELETE'); toast('Pedido removido'); renderOrcamento(); });
}

/* ───────── planos de ação (inline) ───────── */
function planoCell(tipo,chave,cod,desc,dtval){
  const pl=S.planos[chave];
  if(pl&&(pl.acao||pl.responsavel)) return `<span class="rowact plano-set" data-tipo="${tipo}" data-chave="${esc(chave)}" data-cod="${cod}" data-desc="${esc(desc)}" data-dtval="${dtval||''}">${badge((pl.status||'').toLowerCase().replace(/ /g,'_'),pl.acao||pl.status)}</span>`;
  return `<button class="btn sm rowact plano-set" data-tipo="${tipo}" data-chave="${esc(chave)}" data-cod="${cod}" data-desc="${esc(desc)}" data-dtval="${dtval||''}">+ plano</button>`;
}
function wirePlanoCells(){ document.querySelectorAll('.plano-set').forEach(b=>b.onclick=e=>{e.stopPropagation();modalPlano({cod:+b.dataset.cod,desc:b.dataset.desc,tipo:b.dataset.tipo,chave:b.dataset.chave,dtval:b.dataset.dtval||null});}); }

/* ───────── modais ───────── */
function openModal(html){ $('#modal').innerHTML=html; $('#modal-bg').classList.add('on'); }
function closeModal(){ $('#modal-bg').classList.remove('on'); }
function modalMeta(r){ openModal(`<h3>Meta de compras — ${r.mes}</h3>
  <label>Valor da meta (R$)</label><input type="number" id="m-meta" value="${r.meta||''}" step="1000">
  <div class="m-acts"><button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Salvar</button></div>`);
  $('#m-cancel').onclick=closeModal;
  $('#m-ok').onclick=async()=>{ await postJSON('/api/orcamento/meta',{mes:r.mes,comprador:r.comprador,meta_valor:+$('#m-meta').value||0}); closeModal(); toast('Meta salva'); renderOrcamento(); };
}
function modalPedido(prod){ // prod opcional (pré-preenche fornecedor + valor sugerido)
  const comp=S.compradorNome||'TODOS', hoje=new Date().toISOString().slice(0,10);
  const vsug=prod?((prod.sugestao_compra||0)*(prod.custo_unit||0)):'';
  const dl=(S.fornecedores||[]).map(o=>`<option value="${esc(o.fornecedor)}">`).join('');
  openModal(`<h3>Novo pedido de compra</h3>
    <label>Data</label><input type="date" id="pd-data" value="${hoje}">
    <label>Fornecedor</label><input type="text" id="pd-forn" list="pd-forn-dl" autocomplete="off" placeholder="digite e selecione…" value="${prod?esc(prod.fornecedor||''):''}"><datalist id="pd-forn-dl">${dl}</datalist>
    <label>Nº pedido</label><input type="text" id="pd-num">
    <label>Valor (R$)</label><input type="number" id="pd-valor" value="${vsug?vsug.toFixed(2):''}" step="0.01">
    <label>Prazo pagamento (dias)</label><input type="number" id="pd-prazo">
    <div class="m-acts"><button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Lançar</button></div>`);
  $('#m-cancel').onclick=closeModal;
  $('#m-ok').onclick=async()=>{
    const nome=($('#pd-forn').value||'').trim();
    const match=(S.fornecedores||[]).find(x=>(x.fornecedor||'').toLowerCase()===nome.toLowerCase());
    if(nome && !match && !confirm('Fornecedor não está na lista. Lançar mesmo assim com o texto digitado?')) return;
    const cod=match?match.codfornec:(prod?prod.codfornec:null);
    await postJSON('/api/pedidos',{data_pedido:$('#pd-data').value,comprador:comp,codfornec:cod,fornecedor:match?match.fornecedor:nome,n_pedido:$('#pd-num').value,valor:+$('#pd-valor').value||0,prazo_dias:+$('#pd-prazo').value||null});
    closeModal(); toast('Pedido lançado'); if(S.view==='orcamento')renderOrcamento(); };
}
function modalPedidoFornecedor(gr){ // loop: gera 1 pedido com a soma da sugestão do fornecedor
  const comp=S.compradorNome||'TODOS', hoje=new Date().toISOString().slice(0,10);
  openModal(`<h3>Gerar pedido — ${esc(gr.forn)}</h3>
    <div class="count-line">${gr.itens.length} itens sugeridos · total ${money(gr.valor)}</div>
    <label>Data</label><input type="date" id="pd-data" value="${hoje}">
    <label>Nº pedido</label><input type="text" id="pd-num">
    <label>Valor (R$)</label><input type="number" id="pd-valor" value="${gr.valor.toFixed(2)}" step="0.01">
    <label>Prazo pagamento (dias)</label><input type="number" id="pd-prazo">
    <div class="m-acts"><button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Lançar no orçamento</button></div>`);
  $('#m-cancel').onclick=closeModal;
  $('#m-ok').onclick=async()=>{ await postJSON('/api/pedidos',{data_pedido:$('#pd-data').value,comprador:comp,codfornec:gr.cod,fornecedor:gr.forn,n_pedido:$('#pd-num').value,valor:+$('#pd-valor').value||0,prazo_dias:+$('#pd-prazo').value||null}); closeModal(); toast('Pedido lançado no orçamento ✓'); };
}
function modalPlano(it){
  const chave=it.chave||(it.tipo==='validade'?(it.cod+'|'+(it.lote?it.lote.dtval:it.dtval)):String(it.cod));
  const dtval=it.dtval||(it.lote?it.lote.dtval:null);
  const pl=S.planos[chave]||{};
  openModal(`<h3>Plano de ação</h3><div class="count-line">${esc(it.desc||'')}</div>
    <label>Responsável</label><input type="text" id="pl-resp" value="${esc(pl.responsavel||'')}">
    <label>Ação</label><input type="text" id="pl-acao" value="${esc(pl.acao||'')}" placeholder="ex.: ENCARTE, DEVOLUÇÃO, BONIFICAÇÃO">
    <label>Prazo</label><input type="date" id="pl-prazo" value="${pl.prazo?String(pl.prazo).slice(0,10):''}">
    <label>Status</label><select id="pl-status"><option ${pl.status==='PENDENTE'?'selected':''}>PENDENTE</option><option ${pl.status==='EM ANDAMENTO'?'selected':''}>EM ANDAMENTO</option><option ${pl.status==='CONCLUIDO'?'selected':''}>CONCLUIDO</option></select>
    <div class="m-acts">${pl.acao?`<button class="btn" id="m-del">Excluir</button>`:''}<button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Salvar</button></div>`);
  $('#m-cancel').onclick=closeModal;
  if($('#m-del'))$('#m-del').onclick=async()=>{ await postJSON('/api/planos/'+encodeURIComponent(chave),{}, 'DELETE'); delete S.planos[chave]; closeModal(); toast('Plano removido'); render(); };
  $('#m-ok').onclick=async()=>{ const d={chave,tipo:it.tipo,codprod:it.cod,dtvalidade:dtval,descricao:it.desc,responsavel:$('#pl-resp').value,acao:$('#pl-acao').value,prazo:$('#pl-prazo').value||null,status:$('#pl-status').value};
    await postJSON('/api/planos',d); S.planos[chave]=d; closeModal(); toast('Plano salvo ✓'); render(); };
}

/* ───────── plano de reposição (360°) ───────── */
function planoDrawer(plano){
  if(!plano||plano.sem_giro||!plano.semanas||!plano.semanas.length) return '';
  const libs=plano.liberacoes||[];
  const prox=libs[0];
  const resumo=prox
    ? `Próximo pedido: <b>${int(prox.qt)} un</b> (${money(prox.valor)}) — ${prox.semana===0?'<b>sair agora</b>':'liberar em '+dt(prox.data)+' (sem. +'+prox.semana+')'}`
    : 'Sem necessidade de pedido no horizonte.';
  return `<div class="d-sec">Plano no tempo (12 sem.)</div>
    <div class="count-line">${resumo}${plano.inbound_zero?' · <span class="muted">sem trânsito no BI; reabastecimento planejado</span>':''}</div>
    <div class="chart-box sm" style="height:170px"><canvas id="d-plano"></canvas></div>`;
}
function buildPlanoChart(plano){
  if(!plano||plano.sem_giro||!plano.semanas||!plano.semanas.length) return;
  const W=plano.semanas, seg=plano.estoque_seguranca||0;
  const labels=W.map(w=>'S'+w.semana);
  const saldo=W.map(w=>w.saldo_proj), receb=W.map(w=>(w.receb_prog||0)+(w.receb_plan||0));
  chart('d-plano',{data:{labels,datasets:[
      {type:'bar',label:'Recebimentos',data:receb,backgroundColor:C.accent2,borderRadius:4,order:2},
      {type:'line',label:'Saldo projetado',data:saldo,borderColor:C.accent,backgroundColor:'transparent',tension:.25,pointRadius:2,order:1},
      {type:'line',label:'Estoque segurança',data:W.map(()=>seg),borderColor:C.orange,borderDash:[5,4],pointRadius:0,borderWidth:1.5,order:0},
    ]},options:{plugins:{legend:{display:true,labels:{boxWidth:10,font:{size:9}}},tooltip:{callbacks:{label:c=>c.dataset.label+': '+int(c.raw)}}},
      scales:{y:{beginAtZero:false,ticks:{callback:v=>int(v)}}}}});
}

/* ───────── produto 360 ───────── */
async function openProduto(cod){
  const ov=$('#overlay'),dr=$('#drawer'); ov.classList.add('on'); dr.classList.add('on'); dr.innerHTML='<div class="loader"><div class="spinner"></div></div>';
  try{
    const j=await getJSON('/api/produto/'+cod+'?'+serverQS());
    if(!j.produto){ dr.innerHTML='<span class="d-close">×</span><div class="empty">Produto sem posição.</div>'; wireDrawer(); return; }
    const p=j.produto,lotes=j.lotes||[];
    const cobPct=p.cobertura!=null?Math.min(100,p.cobertura/(S.params.cob*2)*100):0;
    dr.innerHTML=`<span class="d-close">×</span>
      <h2>${esc(p.descricao)}</h2>
      <div class="d-cod">cód ${p.codprod} · ${esc(p.fornecedor||'')} · ${badge(p.curva_abc)} ${badge(p.xyz)} · ${esc((p.comprador||'').split(' ')[0]||'')}</div>
      <div class="d-kpis">
        <div class="d-kpi"><div class="l">Disponível</div><div class="v">${int(p.qtdisp)}</div></div>
        <div class="d-kpi"><div class="l">Valor</div><div class="v">${money(p.valor)}</div></div>
        <div class="d-kpi"><div class="l">Giro/mês ${spark((p.serie_mensal&&p.serie_mensal.length?p.serie_mensal:p.serie_giro))}</div><div class="v">${int(p.giro_mes)}</div></div>
        <div class="d-kpi"><div class="l">Cobertura</div><div class="v">${cob(p.cobertura)}</div></div>
      </div>
      <div class="bar"><i style="width:${cobPct}%"></i></div>
      ${p.giro_fonte==='forecast'?`<div class="count-line">Giro por <b>forecast (RCA, ${S.params.fcmeses}m)</b>: ${int(p.giro_forecast)}/mês · média 3m (oficial): ${int(p.giro_media3)}/mês</div>`:''}
      ${p.giro_fonte==='sazonal'?`<div class="count-line">Giro por <b>forecast sazonal (RCA, 24m)</b>: ${int(p.giro_mes)}/mês${p.fatores_sazonais?` · fator do mês ${dec(p.fatores_sazonais[new Date().getMonth()+1]||1,2)}×`:''} · média 3m (oficial): ${int(p.giro_media3)}/mês</div>`:''}
      <div class="d-sec">Venda no período</div>
      <div class="lote-row"><span>Venda</span><span>${money(p.venda)}</span></div>
      <div class="lote-row"><span>Lucro</span><span>${money(p.lucro)} ${p.margem!=null?`<small class="muted">(${dec(p.margem,1)}%)</small>`:''}</span></div>
      <div class="lote-row"><span>Qtd vendida</span><span>${int(p.qtd_vendida)}</span></div>
      <div class="d-sec">Situação</div>
      <div class="lote-row"><span>Abastecimento</span><span>${badge(p.status_abast)}</span></div>
      <div class="lote-row"><span>Ruptura</span><span>${p.status_ruptura?badge('0-15',p.status_ruptura+'d'):'—'}</span></div>
      <div class="lote-row"><span>Parado</span><span>${p.status_parado?badge(p.status_parado):badge('ok','ok')}</span></div>
      <div class="lote-row"><span>Última saída</span><span>${dt(p.dtultsaida)} ${p.dias_sem_venda!=null?'('+p.dias_sem_venda+'d)':''}</span></div>
      <div class="d-sec">Reposição (lead ${int(p.lead_efetivo)}d)</div>
      <div class="lote-row"><span>Ponto de pedido</span><span>${int(p.rop)}</span></div>
      <div class="lote-row"><span>Estoque alvo</span><span>${int(p.est_alvo)}</span></div>
      <div class="lote-row"><span><b>Sugestão de compra</b></span><span><b>${sugCx(p.sugestao_compra,p.qtunitcx)}</b></span></div>
      ${planoDrawer(p.plano)}
      <div class="d-sec">Lotes / validade</div>
      ${lotes.length?lotes.map(l=>`<div class="lote-row"><span>${dt(l.dtval)} · lote ${esc(l.numlote)}</span><span class="lr-r">${int(l.qt)} un · ${l.dias_para_vencer}d ${badge(l.classificacao)}</span></div>`).join(''):'<div class="muted" style="font-size:.8rem">Sem lotes endereçados.</div>'}
      ${(p.sugestao_compra||0)>0?`<div class="m-acts"><button class="btn primary" id="d-pedido">Registrar pedido</button></div>`:''}`;
    wireDrawer(); if($('#d-pedido'))$('#d-pedido').onclick=()=>{closeDrawer();modalPedido(p);};
    buildPlanoChart(p.plano);
  }catch(e){ dr.innerHTML='<span class="d-close">×</span><div class="empty">Erro: '+e.message+'</div>'; wireDrawer(); }
}
function wireDrawer(){ $('#drawer .d-close').onclick=closeDrawer; }
function closeDrawer(){ $('#overlay').classList.remove('on'); $('#drawer').classList.remove('on'); }

/* ───────── dispatch ───────── */
function render(){
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  $('#v-'+S.view).classList.add('active');
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===S.view));
  if(S.view==='orcamento'){ renderOrcamento(); return; }
  if(S.view==='plano'){ renderPlano(); savePrefs(); return; }
  const P=filtered();
  ({fila:renderFila,cockpit:renderCockpit,ruptura:renderRuptura,reposicao:renderReposicao,validade:()=>renderValidade(),parado:renderParado,comprasvendas:renderComprasVendas,abcxyz:renderABCXYZ,fornecedores:renderFornecedores,produtos:renderProdutos}[S.view])(P);
  savePrefs();
}
function goView(view,filt){ S.view=view; filt=filt||{}; S.cli.abast=filt.abast||''; S.cli.parado=filt.parado||''; S.cli.ruptura=filt.ruptura||''; if(filt.curva!=null){S.cli.curva=filt.curva;$('#f-curva').value=filt.curva;} render(); }

/* ───────── boot ───────── */
async function init(){
  const pr=loadPrefs();
  if(pr.base) S.base=pr.base; if(pr.vperiodo) S.vperiodo=pr.vperiodo; if(pr.params) S.params={...S.params,...pr.params};
  try{
    const f=await getJSON('/api/filtros');
    S.filiaisAll=f.filiais;
    const fsel=(pr.filiais&&pr.filiais.length)?pr.filiais:(f.filiais_padrao||f.filiais);
    S.filiaisSel=new Set(fsel);
    $('#f-filiais').innerHTML=f.filiais.map(x=>`<span class="chip ${S.filiaisSel.has(x)?'on':''}" data-f="${x}">${x}</span>`).join('');
    S.fornecedores=f.fornecedores||[];
    $('#f-fornec-dl').innerHTML=f.fornecedores.map(o=>`<option value="${esc(o.fornecedor)}">`).join('');
    $('#f-depto').innerHTML+=f.deptos.map(d=>`<option value="${d}">${d}</option>`).join('');
    $('#f-comprador').innerHTML='<option value="">Empresa toda</option>'+f.compradores.filter(c=>c.codcomprador>0).map(c=>`<option value="${c.codcomprador}">${esc(c.comprador)}</option>`).join('');
    if(pr.comprador){ S.cli.comprador=pr.comprador; $('#f-comprador').value=pr.comprador; S.compradorNome=$('#f-comprador').selectedOptions[0]?.textContent||''; }
  }catch(e){ toast('Falha nos filtros: '+e.message,true); }
  // base toggle visual
  $('#f-base').querySelectorAll('.seg-opt').forEach(o=>o.classList.toggle('on',o.dataset.v===S.base));
  // params inputs
  $('#p-lead').value=S.params.lead; $('#p-seg').value=S.params.seg; $('#p-cob').value=S.params.cob; $('#p-hor').value=S.params.hor;
  $('#p-parado').value=S.params.parado; $('#p-fcmeses').value=S.params.fcmeses;
  const giroModo=()=>S.params.sazonal?2:(S.params.forecast?1:0);  // 0=media3 1=forecast 2=sazonal
  $('#p-forecast').querySelectorAll('.seg-opt').forEach(o=>o.classList.toggle('on',+o.dataset.v===giroModo()));
  $('#p-forecast').querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{const v=+o.dataset.v;S.params.forecast=v>=1?1:0;S.params.sazonal=v===2?1:0;$('#p-forecast').querySelectorAll('.seg-opt').forEach(x=>x.classList.toggle('on',x===o));});
  $('#p-arredcx').querySelectorAll('.seg-opt').forEach(o=>o.classList.toggle('on',+o.dataset.v===(S.params.arredondacx?1:0)));
  $('#p-arredcx').querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{S.params.arredondacx=+o.dataset.v;$('#p-arredcx').querySelectorAll('.seg-opt').forEach(x=>x.classList.toggle('on',x===o));});

  // comprador → client filter + define visão inicial
  $('#f-comprador').onchange=e=>{ S.cli.comprador=e.target.value; S.compradorNome=e.target.selectedOptions[0]?.textContent||''; if(e.target.value&&S.view==='cockpit')S.view='fila'; render(); };
  $('#f-filiais').querySelectorAll('.chip').forEach(ch=>ch.onclick=()=>{const v=ch.dataset.f;ch.classList.toggle('on');ch.classList.contains('on')?S.filiaisSel.add(v):S.filiaisSel.delete(v); if(!S.filiaisSel.size){ch.classList.add('on');S.filiaisSel.add(v);return;} loadData();});
  $('#f-base').querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{S.base=o.dataset.v;$('#f-base').querySelectorAll('.seg-opt').forEach(x=>x.classList.toggle('on',x===o));loadData();});
  $('#f-vperiodo').value=S.vperiodo; $('#f-vperiodo').onchange=e=>{S.vperiodo=e.target.value;loadData();};
  $('#f-curva').onchange=e=>{S.cli.curva=e.target.value;render();};
  $('#f-xyz').onchange=e=>{S.cli.xyz=e.target.value;render();};
  $('#f-fornec').onchange=e=>{
    const nome=(e.target.value||'').trim();
    const m=nome?(S.fornecedores||[]).find(x=>(x.fornecedor||'').toLowerCase()===nome.toLowerCase()):null;
    S.cli.fornec=m?String(m.codfornec):'';
    if(nome&&!m) e.target.value='';   // texto sem correspondência → volta p/ Todos
    render();
  };
  $('#f-depto').onchange=e=>{S.cli.depto=e.target.value;render();};
  let bt; $('#f-busca').oninput=e=>{clearTimeout(bt);bt=setTimeout(()=>{S.cli.busca=e.target.value;render();},250);};
  $('#btn-params').onclick=()=>{const p=$('#params-panel');p.style.display=p.style.display==='none'?'block':'none';};
  $('#btn-limpar').onclick=()=>{
    S.cli={comprador:'',curva:'',xyz:'',fornec:'',depto:'',busca:'',abast:'',parado:'',ruptura:''};
    S.compradorNome='';
    ['#f-comprador','#f-curva','#f-xyz','#f-fornec','#f-depto'].forEach(s=>{const e=$(s);if(e)e.value='';});
    $('#f-busca').value='';
    render();
  };
  $('#p-apply').onclick=()=>{S.params={lead:+$('#p-lead').value,seg:+$('#p-seg').value,cob:+$('#p-cob').value,hor:+$('#p-hor').value,parado:+$('#p-parado').value||60,forecast:S.params.forecast?1:0,sazonal:S.params.sazonal?1:0,fcmeses:+$('#p-fcmeses').value||6,arredondacx:S.params.arredondacx?1:0};loadData();};
  document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{S.view=t.dataset.view;S.cli.abast='';S.cli.parado='';S.cli.ruptura='';render();});
  $('#overlay').onclick=closeDrawer; $('#modal-bg').onclick=e=>{if(e.target===$('#modal-bg'))closeModal();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeDrawer();closeModal();}});
  if(pr.view) S.view=pr.view;
  setStickTop(); window.addEventListener('resize', setStickTop); window.addEventListener('load', setStickTop);
  loadData();
}
// altura real da topbar+filterbar (ambas sticky) → offset do cabeçalho congelado das tabelas
function setStickTop(){
  const tb=$('.topbar'), fb=$('.filterbar');
  const h=(tb?tb.offsetHeight:0)+(fb?fb.offsetHeight:0);
  if(h) document.documentElement.style.setProperty('--stick-top', h+'px');
}
init();
