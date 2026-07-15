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
  filiaisAll:[], filiaisSel:new Set(), base:'gerencial', vperiodo:'mes', cvDim:'comprador', abcLens:'venda',
  unidade:'atacado', unidadeNome:'Atacado', nomesFilial:{},
  compradorNome:'',
  cli:{comprador:'',curva:'',xyz:'',fornec:'',depto:'',busca:'',abast:[],margem:[],parado:'',ruptura:'',valDias:'',cobFaixa:[],parFaixa:[]},
  params:{lead:10,seg:25,cob:45,hor:30,parado:60,forecast:0,sazonal:0,fcmeses:6,arredondacx:1},
  charts:{}, sort:{}, valFaixa:null,
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
// status executivo do abastecimento (metodologia v3) → rótulo + cor
const STAT_EXEC={ruptura_sem_pedido:['Ruptura s/ pedido','#ef4444'],ruptura_pedido_parcial:['Ruptura · pedido parcial','#f97316'],ruptura_pedido_cobre:['Ruptura · pedido cobre','#eab308'],compra_urgente:['Compra urgente','#ef4444'],compra_alta:['No prazo','#eab308'],compra_complementar:['Compra complementar','#38bdf8'],programar_compra:['Programar compra','#a78bfa'],pedido_cobre:['Pedido cobre','#22c55e'],estoque_ok:['Estoque OK','#22c55e']};
const statExec = v => { const s=STAT_EXEC[v]; return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—'; };
// sugestão em caixas a partir do campo já calculado no servidor (sugestao_cx) + unidades
const sugCxN = p => { if(!(p.sugestao_cx>0)) return '—';
  return (p.caixa>1) ? `${int(p.sugestao_cx)} cx · ${int(p.sugestao_cx*p.caixa)} un` : `${int(p.sugestao_cx)} un`; };
// embalagem do produto (caixa do PCEMBALAGEM) + fator un/cx — p/ validar a conversão unid→caixa
const embCell = p => { const e=esc(p.embalagem_caixa||''); const cx=p.caixa||1;
  return cx>1 ? `${e||'cx'} <small class="muted">· ${int(cx)} un/cx</small>` : `<span class="muted">${e||'avulso'} · 1 un</span>`; };
// navegação em 2 níveis: grupo → telas
const NAV={visao:['cockpit','gerencial'],comprar:['reposicao','estoque_zero','plano'],pedidos:['orcamento'],estoque:['ruptura','parado','validade','ruptura_comprador','ocupacao'],analise:['desempenho','comprasvendas','fornecedores','abcxyz','produtos','qualidade']};
// aba 'logistica' oculta a pedido do diretor (não usa p/ análise) — reversível: re-adicionar em pedidos
const GROUP_OF=v=>Object.keys(NAV).find(g=>NAV[g].includes(v))||'visao';
// filtro Abast. multi-seleção — agora LOCAL da aba Produtos (não é mais global)
const ABAST_LABELS={urgente:'Urgente',alta:'Alta',atencao:'Atenção',excesso:'Excesso',ok:'OK',sem_giro:'Sem giro'};
const abastLabel=arr=>!arr.length?'Todos':(arr.length===1?(ABAST_LABELS[arr[0]]||arr[0]):`${arr.length} status`);
// filtro de margem (aba Produtos) — faixas multi-seleção; margem null (sem venda) vira bucket próprio
const MARGEM_LABELS={neg:'Negativa (<0%)',b0:'0–10%',b10:'10–20%',b20:'20–30%',b30:'30%+',sv:'Sem venda'};
const margemLabel=arr=>!arr.length?'Todas':(arr.length===1?(MARGEM_LABELS[arr[0]]||arr[0]):`${arr.length} faixas`);
const margemBucket=p=>{const m=p.margem; return m==null?'sv':(m<0?'neg':(m<10?'b0':(m<20?'b10':(m<30?'b20':'b30'))));};
// valor em "N cx · M un" (só unidades quando não há caixa ou ≤0) — colunas de estoque em caixa
const cxUn=(v,caixa)=>{ if(v==null) return '—'; const c=caixa||1; return (c>1&&v>0)?`${int(Math.round(v/c))} cx · ${int(v)} un`:int(v); };
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
function savePrefs(){ try{ localStorage.setItem(PREF, JSON.stringify({comprador:S.cli.comprador,base:S.base,vperiodo:S.vperiodo,unidade:S.unidade,params:S.params,view:S.view})); }catch(e){} }
function loadPrefs(){ try{ return JSON.parse(localStorage.getItem(PREF))||{}; }catch(e){ return {}; } }

/* ───────── querystring p/ servidor ───────── */
function serverQS(){
  const p=new URLSearchParams();
  p.set('unidade', S.unidade);
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
    if(snap.unidade_nome) S.unidadeNome=snap.unidade_nome;
    const br=snap.bi_refresh;
    $('#meta-gerado').textContent = (br&&br.end_fmt)
      ? ('BI atualizado '+br.end_fmt+(br.in_progress?' · atualizando…':''))
      : ('Atualizado em '+snap.gerado_em);
    const fnome=f=>S.nomesFilial[f]||f, fils=Array.isArray(snap.filiais)?snap.filiais.map(fnome).join(' + '):snap.filiais;
    $('#meta-filiais').textContent=(snap.unidade_nome||'')+' · '+fils+' · '+snap.n+' itens · gerencial';
  }catch(e){ toast('Falha ao carregar: '+e.message,true); console.error(e); }
  $('#loader').style.display='none'; $('#content').style.display='block';
  render();
}

/* ───────── filtros client-side ───────── */
function filtered(skipCurva){
  const f=S.cli, b=f.busca.trim().toLowerCase();
  return S.produtosAll.filter(p=>{
    if(f.comprador && String(p.codcomprador)!==f.comprador) return false;
    if(!skipCurva && f.curva && p.curva_abc!==f.curva) return false;
    if(f.xyz && p.xyz!==f.xyz) return false;
    if(f.fornec && String(p.codfornec)!==f.fornec) return false;
    if(f.depto && String(p.codepto)!==f.depto) return false;
    if(f.parado && p.status_parado!==f.parado) return false;
    if(f.ruptura && !p.status_ruptura) return false;
    if(b && !(String(p.codprod).includes(b)||(p.descricao||'').toLowerCase().includes(b))) return false;
    return true;
  });
}
function lotesFiltrados(){
  const f=S.cli, b=(f.busca||'').trim().toLowerCase();
  let L=S.validade?.lotes||[];
  if(f.comprador){
    const cods=new Set(S.produtosAll.filter(p=>String(p.codcomprador)===f.comprador).map(p=>p.codprod));
    L=L.filter(l=>cods.has(l.codprod));
  }
  if(b) L=L.filter(l=>String(l.codprod).includes(b)||(l.descricao||'').toLowerCase().includes(b));
  return L;
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
  const zerados=P.filter(p=>p.estoque_zero&&(p.giro_dia||0)>0);   // ruptura real (estoque ≤ 0 e giro > 0)
  const faixas=FAIXAS.map(([n,lo,hi])=>{const it=comGiro.filter(p=>p.cobertura!=null&&Math.ceil(p.cobertura)>=lo&&Math.ceil(p.cobertura)<=hi);return{faixa:n,qt:it.length,valor:sum(it,p=>p.valor)};});
  faixas.push({faixa:'sem giro',qt:semGiro.length,valor:sum(semGiro,p=>p.valor)});
  const abc={}; ['A','B','C'].forEach(c=>{const it=P.filter(p=>p.curva_abc===c);abc[c]={qt:it.length,valor:sum(it,p=>p.valor),venda:sum(it,p=>p.venda)};});
  const matriz={}; P.forEach(p=>{if(p.abc_xyz){(matriz[p.abc_xyz]=matriz[p.abc_xyz]||{qt:0,valor:0,venda:0});matriz[p.abc_xyz].qt++;matriz[p.abc_xyz].valor+=(p.valor||0);matriz[p.abc_xyz].venda+=(p.venda||0);}});
  const cnt=(fld,v)=>{const it=P.filter(p=>p[fld]===v);return{qt:it.length,valor:sum(it,p=>p.valor)};};
  const venda_total=sum(P,p=>p.venda), lucro_total=sum(P,p=>p.lucro);
  return {valor_total,venda_total,lucro_total,margem_total: venda_total?lucro_total/venda_total*100:null,
    n:P.length,com_estoque:P.filter(p=>(p.qtdisp||0)>0).length,com_giro:comGiro.length,sem_giro:semGiro.length,
    valor_parado:sum(parados,p=>p.valor),valor_sem_giro:sum(semGiro,p=>p.valor),faixas,abc,matriz,
    parado:{atencao:cnt('status_parado','atencao'),critico:cnt('status_parado','critico'),muito_critico:cnt('status_parado','muito_critico')},
    ruptura:{total:rupt.length,valor:sum(rupt,p=>p.valor),f0_15:rupt.filter(p=>p.status_ruptura==='0-15').length,
      zerados:zerados.length,valor_zerados:sum(zerados,p=>p.valor)},
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
    cont.querySelectorAll('tbody tr[data-cod]').forEach(tr=>tr.onclick=e=>{ if(e.target.closest('.rowact'))return; (onClickRow||openProduto)(tr.dataset.cod);});
  },0);
  return note+`<div class="tbl-wrap${view==='produtos'?' freeze2':''}"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
const colCod={key:'codprod',label:'Cód',num:true};
const colProd={key:'descricao',label:'Produto',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v)}</span>`};
const colForn={key:'fornecedor',label:'Fornecedor',fmt:v=>`<span class="prod" title="${esc(v)}">${esc(v||'—')}</span>`};
const colGiroSpark={key:'giro_mes',label:'Giro/mês',num:true,html:p=>`${int(p.giro_mes)} ${spark(p.serie_giro)}`};

// ── ordenação clicável p/ tabelas montadas na mão (headers com data-k) ──
function _sortArr(rows,sk){ return [...rows].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=-Infinity;if(y==null)y=-Infinity;
  if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y)); return sk.dir*(x-y);}); }
function sortTh(cols,sk){ return cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join(''); }
function wireSortTbl(container,skKey,onChange){ if(!container)return; container.querySelectorAll('thead th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[skKey]||{};S.sort[skKey]={key:k,dir:cur.key===k?-cur.dir:-1};onChange();}); }

function exportQS(){
  const p=new URLSearchParams(serverQS()), f=S.cli;
  if(f.comprador) p.set('comprador_cod',f.comprador);
  if(f.curva) p.set('curva',f.curva);
  if(f.xyz) p.set('xyz',f.xyz);
  if(f.fornec) p.set('fornec',f.fornec);
  if(f.depto) p.set('depto',f.depto);
  if(f.abast.length && S.view==='produtos') p.set('abast',f.abast.join(','));
  if(f.valDias && S.view==='validade') p.set('val_dias',f.valDias);
  if(S.valFaixa && S.view==='validade'){ p.set('val_faixa_lo',S.valFaixa[0]); p.set('val_faixa_hi',S.valFaixa[1]); }
  if((f.busca||'').trim()) p.set('busca',f.busca.trim());
  if(f.ezStatus) p.set('ez_status',f.ezStatus);
  if(f.cobFaixa && f.cobFaixa.length) p.set('cob_faixa',f.cobFaixa.join(','));
  if(f.parFaixa && f.parFaixa.length && S.view==='parado') p.set('par_faixa',f.parFaixa.join(','));
  if(f.cobSub) p.set('cob_sub',f.cobSub);
  if(f.cobPed) p.set('cob_ped',f.cobPed);
  if(f.parClasse) p.set('par_classe',f.parClasse);
  if(f.fornClasse) p.set('forn_classe',f.fornClasse);
  return p.toString();
}
function exportBtns(view){ const qs=exportQS(); return `<span class="exp"><a class="btn sm" href="/api/export/${view}.xlsx?${qs}">⬇ Excel</a><a class="btn sm" href="/api/export/${view}.pdf?${qs}">⬇ PDF</a></span>`; }
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
     ${kpi('Em ruptura',int(k.ruptura.zerados),'estoque ≤ 0 c/ giro',C.red)}
     ${kpi('A comprar',int(k.repor.n),'sug. '+moneyK(k.repor.valor),C.orange)}
     ${kpi('Capital parado',moneyK(k.valor_parado),dec(k.valor_total?k.valor_parado/k.valor_total*100:0,1)+'% do estoque',C.purple)}
   </div>
   <h2 class="section"><span>Alertas de ação</span></h2>
   <div class="alerts">
     ${alertCard(k.ruptura.zerados,'Em ruptura (estoque ≤ 0)',k.ruptura.valor_zerados,C.red,'estoque_zero',{})}
     ${alertCard(k.ruptura.f0_15,'Cobertura crítica (≤15d)',k.ruptura.valor,C.orange,'ruptura',{cobFaixa:'0-30'})}
     ${alertCard(k.repor.n,'Comprar (cobertura baixa)',k.repor.valor,C.orange,'reposicao',{})}
     ${alertCard(v.critico||0,'Vencimento ≤7 dias',v.valor_risco_critico!=null?v.valor_risco_critico:v.valor_risco,C.yellow,'validade',{})}
     ${alertCard(k.parado.muito_critico.qt,'Parado 120+ dias',k.parado.muito_critico.valor,C.purple,'parado',{parado:'muito_critico'})}
   </div>
   <div class="row">
     <div class="panel grow"><h3>Curva ABC (${S.abcLens==='estoque'?'estoque':'vendas'}) <span class="seg" style="display:inline-flex;vertical-align:middle;margin-left:8px"><span class="seg-opt ${S.abcLens!=='estoque'?'on':''}" data-abclens="venda">Vendas</span><span class="seg-opt ${S.abcLens==='estoque'?'on':''}" data-abclens="estoque">Estoque</span></span></h3>
       <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
         <div class="chart-box sm" style="height:190px;flex:2 1 300px;min-width:0"><canvas id="ch-abc"></canvas></div>
         <div style="flex:1 1 240px;min-width:220px">
           <div class="count-line" style="margin:0 0 8px">Participação dos itens (quantidade)</div>
           <div style="display:flex;align-items:center;gap:16px">
             <div style="position:relative;height:150px;width:150px;flex:none">
               <canvas id="ch-abc-itens"></canvas>
               <div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none">
                 <div style="font-size:1.3rem;font-weight:700;color:var(--text);line-height:1">${int(totItens)}</div>
                 <div style="font-size:.6rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:.6px">itens</div>
               </div>
             </div>
             <div id="abc-itens-leg" style="display:flex;flex-direction:column;gap:10px"></div>
           </div>
         </div>
       </div>
       <table class="mini" style="margin-top:10px">${['A','B','C'].map(c=>{const _v=S.abcLens==='estoque'?k.abc[c].valor:k.abc[c].venda,_t=S.abcLens==='estoque'?k.valor_total:k.venda_total;return `<tr><td>Curva ${c}</td><td class="num">${int(k.abc[c].qt)} itens</td><td class="num">${money(_v)}</td><td class="num">${dec(k.abc[c].qt/totItens*100,0)}% dos itens</td><td class="num">${dec(_t?_v/_t*100:0,0)}% ${S.abcLens==='estoque'?'do estoque':'da venda'}</td></tr>`;}).join('')}</table>
     </div>
   </div>
   <div class="row">
     <div class="panel grow"><h3>Maiores ofensores — capital parado</h3><div id="cp-parado"></div></div>
     <div class="panel grow"><h3>Maiores ofensores — risco de vencimento</h3><div id="cp-venc"></div></div>
   </div>`;
  chart('ch-abc',{type:'bar',data:{labels:['A','B','C'],datasets:[{data:['A','B','C'].map(c=>S.abcLens==='estoque'?k.abc[c].valor:k.abc[c].venda),backgroundColor:[C.green,C.accent,C.dim],borderRadius:6}]},options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+' · '+k.abc[['A','B','C'][c.dataIndex]].qt+' itens'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
  // rosca de participação dos itens por curva (quantidade) — cores fixas A/B/C (verde/azul/cinza), borda = surface p/ respiro
  chart('ch-abc-itens',{type:'doughnut',data:{labels:['Curva A','Curva B','Curva C'],datasets:[{data:['A','B','C'].map(c=>k.abc[c].qt),backgroundColor:[C.green,C.accent,C.dim],borderColor:'#111827',borderWidth:2,hoverOffset:4}]},options:{cutout:'64%',plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>c.label+': '+int(c.raw)+' itens ('+dec(totItens?c.raw/totItens*100:0,1)+'%)'}}}}});
  const _abcLeg=$('#abc-itens-leg'); if(_abcLeg) _abcLeg.innerHTML=['A','B','C'].map((c,i)=>`<div style="display:flex;align-items:center;gap:8px;font-size:.82rem;white-space:nowrap"><span style="width:11px;height:11px;border-radius:3px;background:${[C.green,C.accent,C.dim][i]};flex:none"></span><b>Curva ${c}</b> <span style="color:var(--text-dim)">${int(k.abc[c].qt)} · ${dec(k.abc[c].qt/totItens*100,0)}%</span></div>`).join('');
  document.querySelectorAll('[data-abclens]').forEach(b=>b.onclick=()=>{S.abcLens=b.dataset.abclens;render();});
  const topPar=P.filter(p=>p.status_parado).sort((a,b)=>b.valor-a.valor).slice(0,6);
  const topVen=(S.validade?.lotes||[]).slice().sort((a,b)=>b.valor_risco-a.valor_risco).slice(0,6);
  $('#cp-parado').innerHTML=topPar.map(p=>`<div class="lote-row" data-cod="${p.codprod}" style="cursor:pointer"><span class="prod">${esc(p.descricao)}</span><span class="lr-r">${money(p.valor)}<br><small class="muted">${p.dias_sem_venda==null?'sem saída':p.dias_sem_venda+'d s/ venda'}</small></span></div>`).join('')||'<div class="empty">Nada parado 🎉</div>';
  $('#cp-venc').innerHTML=topVen.map(l=>`<div class="lote-row" data-cod="${l.codprod}" style="cursor:pointer"><span class="prod">${esc(l.descricao)}</span><span class="lr-r">${money(l.valor_risco)}<br><small class="muted">vence ${l.dias_para_vencer}d</small></span></div>`).join('')||'<div class="empty">Sem risco no horizonte 🎉</div>';
  el.querySelectorAll('.lote-row[data-cod]').forEach(r=>r.onclick=()=>openProduto(r.dataset.cod));
  wireAlerts(el);
}

// faixas de cobertura — métrica OFICIAL da planilha (GRAFICO COBERTURA ESTOQUE), faixas fixas
const FX_COB=[{key:'0-30',label:'0-30 · risco ruptura',color:C.red},{key:'31-60',label:'31-60 · OK',color:C.green},
  {key:'61-90',label:'61-90 · atenção',color:C.yellow},{key:'91-120',label:'91-120 · urgente',color:C.orange},
  {key:'121+',label:'121+ · crítico',color:C.purple}];
const cobDiasFmt = v => v==null?'—':(v>=9999?'∞':int(v)+'d');
// filtro de faixa de cobertura é MULTI-seleção (pode marcar várias faixas de uma vez)
const cobFaixaLabel=arr=>!arr.length?'Todas':(arr.length===1?((FX_COB.find(f=>f.key===arr[0])||{}).label||arr[0]):`${arr.length} faixas`);
function cobToggle(k){ const a=S.cli.cobFaixa||[]; S.cli.cobFaixa=a.includes(k)?a.filter(x=>x!==k):[...a,k]; S.cli.cobSub=''; render(); }

function renderRuptura(P){
  // distribuição da cobertura sobre a base inteira (igual à planilha), com valor por faixa
  const faixas=FX_COB.map(f=>{const it=P.filter(p=>p.cobertura_faixa===f.key);
    return{...f,valor:it.reduce((s,p)=>s+(p.valor||0),0),qt:it.length};});
  // colunas em caixa (mesmo padrão da aba Produtos): mantém unidade e ACRESCENTA cx
  P.forEach(p=>{const cx=p.caixa||1; p._giroCx=cx>1?Math.round((p.giro_mes||0)/cx):null; p._dispCx=cx>1?Math.round((p.qtdisp||0)/cx):null;});
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'codcomprador',label:'Comprador',fmt:(v,p)=>esc((p.comprador||'').split(' ')[0]||'—')},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},
    {key:'_dispCx',label:'Disp. cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'valor',label:'Valor estoque',num:true,fmt:money},
    {key:'cobertura_dias',label:'Cob.',num:true,fmt:cobDiasFmt},
    {key:'qtd_ja_pedida',label:'Já ped.',num:true,fmt:v=>v>0?int(v):'—'},
    colGiroSpark,{key:'_giroCx',label:'Giro cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'sugestao_cx',label:'Sugerido',num:true,html:p=>sugCxN(p)},
    {key:'cobertura_faixa',label:'Faixa',badge:true}];
  // no 121+ mostra a natureza (sem giro × excesso real) — separa estoque morto de excesso de compra
  const cf=S.cli.cobFaixa||[];
  const is121=cf.length===1&&cf[0]==='121+';
  if(is121) cols.push({key:'_tipo',label:'Tipo',html:p=>p.sem_giro
    ?'<span class="badge" style="background:#64748b22;color:#94a3b8">sem giro</span>'
    :'<span class="badge" style="background:#c084fc22;color:#c084fc">excesso</span>'});
  let rf=P;
  if(cf.length) rf=rf.filter(p=>cf.includes(p.cobertura_faixa));
  if(is121&&S.cli.cobSub==='semgiro') rf=rf.filter(p=>p.sem_giro);
  if(is121&&S.cli.cobSub==='excesso') rf=rf.filter(p=>p.excesso_real);
  if(S.cli.cobPed==='com') rf=rf.filter(p=>(p.qtd_ja_pedida||0)>0);
  if(S.cli.cobPed==='sem') rf=rf.filter(p=>(p.qtd_ja_pedida||0)<=0);
  rf=[...rf].sort((a,b)=>(b.valor||0)-(a.valor||0));  // maior estoque primeiro (P1)
  const semG=P.filter(p=>p.cobertura_faixa==='121+'&&p.sem_giro), exc=P.filter(p=>p.excesso_real);
  const el=$('#v-ruptura');
  el.innerHTML=head('Cobertura de estoque por faixa','ruptura')+
    resumoFaixasBlock('Por faixa de cobertura (valor de estoque)',faixas,P,p=>p.valor,cf,'ch-cob')+
    `<div class="row" style="gap:14px;margin-bottom:4px">
       <div class="fb-group"><label>Faixa <small class="muted">(marque várias)</small></label>
         <details class="ms" id="cob-faixa"><summary class="fb-control" style="width:auto">${cobFaixaLabel(cf)}</summary>
           <div class="ms-menu">${FX_COB.map(f=>`<label><input type="checkbox" value="${f.key}" ${cf.includes(f.key)?'checked':''}>${f.label}</label>`).join('')}</div>
         </details></div>
       ${is121?`<div class="fb-group"><label>121+ · natureza</label>
         <select id="cob-sub" class="fb-control" style="width:auto">
           <option value="">Tudo (${int(semG.length+exc.length)})</option>
           <option value="semgiro" ${S.cli.cobSub==='semgiro'?'selected':''}>Sem giro (${int(semG.length)})</option>
           <option value="excesso" ${S.cli.cobSub==='excesso'?'selected':''}>Excesso real (${int(exc.length)})</option>
         </select></div>`:''}
       <div class="fb-group"><label>Pedido em aberto</label>
         <select id="cob-ped" class="fb-control" style="width:auto">
           <option value="">Todos</option>
           <option value="sem" ${S.cli.cobPed==='sem'?'selected':''}>Sem pedido (risco real)</option>
           <option value="com" ${S.cli.cobPed==='com'?'selected':''}>Já comprado</option>
         </select></div>
     </div>
     <div class="count-line">Cobertura = <b>ARREDONDA.CIMA(estoque ÷ giro diário)</b>; giro 0 → não calculável (cai em 121+). Métrica oficial da planilha. Ordene por <b>Valor estoque</b> p/ atacar maior capital. No <b>121+</b>, "sem giro" é estoque morto (liquidar) e "excesso real" é cobertura alta (reduzir compra).</div>`+renderTable(rf,cols,'ruptura');
  drawFaixaChart('ch-cob',faixas,f=>cobToggle(f.key));
  el.querySelectorAll('.vfx[data-fkey]').forEach(d=>d.onclick=()=>cobToggle(d.dataset.fkey));
  wirePorComprador(el);
  const fx=$('#cob-faixa'); if(fx) fx.addEventListener('change',()=>{S.cli.cobFaixa=[...fx.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value);S.cli.cobSub='';render();});
  const sb=$('#cob-sub'); if(sb) sb.onchange=e=>{S.cli.cobSub=e.target.value;render();};
  const pd=$('#cob-ped'); if(pd) pd.onchange=e=>{S.cli.cobPed=e.target.value;render();};
}

function renderEstoqueZero(P){
  const z=P.filter(p=>(p.qtdisp||0)<=0);
  const neg=z.filter(p=>(p.qtdisp||0)<0), comGiro=z.filter(p=>(p.giro_dia||0)>0), comPed=z.filter(p=>(p.qtd_ja_pedida||0)>0);
  // impacto financeiro da ruptura (a custo): volume parado/mês + custo de repor até o alvo
  const vendaPerdida=comGiro.reduce((s,p)=>s+(p.venda_perdida||0),0);
  const custoRepor=comGiro.reduce((s,p)=>s+(p.sugestao_compra||0)*(p.custo_unit||0),0);
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},
    {key:'codcomprador',label:'Comprador',fmt:(v,p)=>esc((p.comprador||'').split(' ')[0]||'—')},
    {key:'qtdisp',label:'Estoque',num:true,html:p=>cxUn(p.qtdisp,p.caixa)},
    {key:'dias_sem_venda',label:'Dias s/ venda',num:true,fmt:v=>v==null?'nunca':int(v)},
    {key:'qtd_ja_pedida',label:'Já ped.',num:true,html:p=>p.qtd_ja_pedida>0?cxUn(p.qtd_ja_pedida,p.caixa):'—'},
    {key:'giro_mes',label:'Giro/mês',num:true,html:p=>`${cxUn(p.giro_mes,p.caixa)} ${spark(p.serie_giro)}`},
    {key:'sugestao_cx',label:'Sugerido (cx)',num:true,html:p=>sugCxN(p)},
    {key:'status_exec',label:'Status',html:p=>statExec(p.status_exec)}];
  const statuses=[...new Set(z.map(p=>p.status_exec))];
  const zf=S.cli.ezStatus?z.filter(p=>p.status_exec===S.cli.ezStatus):z;
  $('#v-estoque_zero').innerHTML=head('Estoque zerado e negativo','estoque_zero')+
    `<div class="kpi-grid" style="grid-template-columns:repeat(5,1fr)">
       ${kpi('Zerados / negativos',int(z.length),int(neg.length)+' negativos',C.red)}
       ${kpi('Com giro (ruptura real)',int(comGiro.length),'precisam repor',C.orange)}
       ${kpi('Já com pedido',int(comPed.length),'aguardando entrega',C.accent)}
       ${kpi('Venda perdida (ruptura)',money(vendaPerdida),'dias em ruptura × giro × preço de venda',C.purple)}
       ${kpi('Custo de reposição',money(custoRepor),'repor até o alvo',C.accent2)}
     </div>
     <div class="fb-group" style="margin:0 0 6px"><label>Filtrar status</label>
       <select id="ez-status" class="fb-control" style="width:auto">
         <option value="">Todos</option>
         ${statuses.map(s=>`<option value="${s}" ${S.cli.ezStatus===s?'selected':''}>${STAT_EXEC[s]?STAT_EXEC[s][0]:s}</option>`).join('')}
       </select></div>
     <div class="count-line">Todos os produtos com estoque (gerencial) ≤ 0. "Já ped." = pedido de compra real em aberto (Winthor).</div>`+
    renderTable(zf,cols,'estoque_zero');
  const sel=$('#ez-status'); if(sel) sel.onchange=e=>{S.cli.ezStatus=e.target.value;render();};
}

const QUAL_CHK={
  sem_custo:{lbl:'Sem custo',cor:'red',fn:p=>(p.custo_unit||0)<=0},
  sem_fornecedor:{lbl:'Sem fornecedor',cor:'orange',fn:p=>p.codfornec==null},
  sem_comprador:{lbl:'Sem comprador',cor:'purple',fn:p=>p.codcomprador==null},
  sem_giro:{lbl:'Sem giro c/ estoque',cor:'yellow',fn:p=>(p.giro_dia||0)<=0&&(p.qtdisp||0)>0},
  estoque_negativo:{lbl:'Estoque negativo',cor:'red',fn:p=>(p.qtdisp||0)<0},
};
function renderQualidade(P){
  const keys=Object.keys(QUAL_CHK);
  const probs=p=>keys.filter(k=>QUAL_CHK[k].fn(p));
  const cat=S.cli.qualCat||'';
  let flagged=P.map(p=>({p,probs:probs(p)})).filter(x=>x.probs.length);
  if(cat) flagged=flagged.filter(x=>x.probs.includes(cat));
  flagged.sort((a,b)=>b.probs.length-a.probs.length);
  const counts={}; keys.forEach(k=>counts[k]=P.filter(QUAL_CHK[k].fn).length);
  const badge1=k=>`<span class="badge" style="background:${C[QUAL_CHK[k].cor]}22;color:${C[QUAL_CHK[k].cor]}">${QUAL_CHK[k].lbl}</span>`;
  const card=k=>`<div class="card kpi" data-cat="${k}" style="cursor:pointer;outline:${cat===k?'2px solid '+C[QUAL_CHK[k].cor]:'none'}">
    <div class="k-label"><span class="dot" style="background:${C[QUAL_CHK[k].cor]}"></span>${QUAL_CHK[k].lbl}</div>
    <div class="k-value">${int(counts[k])}</div></div>`;
  $('#v-qualidade').innerHTML=head('Qualidade da base — produtos com cadastro/saldo inconsistente')+
    `<div class="kpi-grid">${keys.map(card).join('')}</div>
     <div class="count-line">${int(flagged.length)} produtos${cat?` na categoria <b>${QUAL_CHK[cat].lbl}</b> · <a href="#" id="qual-clear">limpar</a>`:' com ao menos um problema'}. Corrigir na origem (Winthor) melhora todas as telas.</div>
     <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Fornecedor</th><th>Comprador</th><th class="num">Estoque</th><th class="num">Custo</th><th class="num">Giro/mês</th><th>Problemas</th></tr></thead>
     <tbody>${flagged.slice(0,400).map(({p,probs})=>`<tr data-cod="${p.codprod}"><td class="num">${p.codprod}</td><td><span class="prod">${esc(p.descricao)}</span></td><td><span class="prod">${esc(p.fornecedor||'—')}</span></td><td>${esc((p.comprador||'').split(' ')[0]||'—')}</td><td class="num">${int(p.qtdisp)}</td><td class="num">${p.custo_unit?money(p.custo_unit):'—'}</td><td class="num">${int(p.giro_mes)}</td><td>${probs.map(badge1).join(' ')}</td></tr>`).join('')}</tbody></table>
     ${flagged.length>400?`<div class="count-line">Mostrando 400 de ${int(flagged.length)}.</div>`:''}</div>`;
  const el=$('#v-qualidade');
  el.querySelectorAll('[data-cat]').forEach(c=>c.onclick=()=>{const k=c.dataset.cat;S.cli.qualCat=(cat===k)?'':k;render();});
  const qc=$('#qual-clear'); if(qc)qc.onclick=e=>{e.preventDefault();S.cli.qualCat='';render();};
  el.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
}

function renderReposicao(P){
  const rep=P.filter(p=>(p.sugestao_compra||0)>0&&(p.giro_dia||0)>0&&!p.compra_suspensa);
  const suspensos=P.filter(p=>p.compra_suspensa).sort((a,b)=>(b.sugestao_compra*b.custo_unit)-(a.sugestao_compra*a.custo_unit));
  // agrupa por fornecedor (+ cubagem do pedido sugerido = Σ caixas sugeridas × volume da caixa)
  const cubItem=p=>(p.sugestao_cx||0)*(p.cubagem_caixa_m3||0);
  const pesoItem=p=>(p.sugestao_cx||0)*(p.peso_caixa_kg||0);
  const g={}; rep.forEach(p=>{(g[p.codfornec]=g[p.codfornec]||{cod:p.codfornec,forn:p.fornecedor||('Forn '+p.codfornec),itens:[],valor:0,cub:0,peso:0}); g[p.codfornec].itens.push(p); g[p.codfornec].valor+=(p.valor_sugerido_liq||0); g[p.codfornec].cub+=cubItem(p); g[p.codfornec].peso+=pesoItem(p);});
  const grupos=Object.values(g).sort((a,b)=>b.valor-a.valor);
  const el=$('#v-reposicao');
  el.innerHTML=head('Abastecimento — o que comprar (por fornecedor)','reposicao')+
    `<div class="count-line">Sugestão líquida = estoque-alvo (giro/dia × (lead + ${int(S.params.cob)}d)) − estoque projetado (disponível + <b>pedido real em aberto</b>), arredondada em <b>caixas</b>. <b>m³</b> = cubagem do pedido sugerido (caixas × volume da caixa). O <b>lead</b> entra na conta (o estoque cai até a mercadoria chegar) e usa o prazo do fornecedor quando houver.</div>`+
    grupos.slice(0,40).map(gr=>`
      <div class="panel forn-grp">
        <h3><span>${esc(gr.forn)} <small class="muted">· ${gr.itens.length} itens${gr.cub>0?` · ${dec(gr.cub,2)} m³`:''}${gr.peso>0?` · ${dec(gr.peso,1)} kg`:''}</small></span>
          <span>${money(gr.valor)} <button class="btn sm primary rowact" data-fornped="${gr.cod}">Gerar pedido</button></span></h3>
        <div class="tbl-wrap"><table><thead><tr><th>Cód</th><th>Produto</th><th>Embalagem</th><th class="num">Disp.</th><th class="num">Já ped.</th><th class="num">Cob.proj</th><th class="num">Giro/mês</th><th class="num">Sugerido (cx)</th><th class="num">m³</th><th class="num">Valor sug.</th><th>Status</th></tr></thead>
        <tbody>${gr.itens.sort((a,b)=>(a.cobertura_proj||0)-(b.cobertura_proj||0)).map(p=>`<tr data-cod="${p.codprod}"><td class="num">${p.codprod}</td><td><span class="prod">${esc(p.descricao)}</span></td><td>${embCell(p)}</td><td class="num">${int(p.qtdisp)}</td><td class="num">${p.qtd_ja_pedida>0?int(p.qtd_ja_pedida):'—'}</td><td class="num">${cob(p.cobertura_proj)}</td><td class="num">${int(p.giro_mes)}</td><td class="num">${sugCxN(p)}</td><td class="num">${cubItem(p)>0?dec(cubItem(p),3):'—'}</td><td class="num">${money(p.valor_sugerido_liq)}</td><td>${statExec(p.status_exec)}</td></tr>`).join('')}</tbody></table></div>
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
    {key:'curva_abc',label:'ABC',badge:true},
    {key:'numlote',label:'Lote'},{key:'dtval',label:'Validade',fmt:dt},{key:'dias_para_vencer',label:'Dias',num:true},
    {key:'qt',label:'Qtd',num:true,fmt:int},{key:'saldo_proj',label:'Saldo proj.',num:true,fmt:int},
    {key:'valor_risco',label:'Valor risco',num:true,fmt:money},{key:'classificacao',label:'Classe',badge:true},
    {key:'_plano',label:'Ação',html:l=>planoCell('validade',l.codprod+'|'+l.dtval,l.codprod,l.descricao,l.dtval)}];
  // faixas — por faixa: valor de estoque (bruto qtd×custo), valor em risco (projetado) e nº lotes
  const faixas=[['0-15',0,15],['16-30',16,30],['31-60',31,60],['61-90',61,90],['90+',91,1e9]];
  const fd=faixas.map(([n,lo,hi])=>{const it=L.filter(l=>l.dias_para_vencer>=lo&&l.dias_para_vencer<=hi);
    return{n,qt:it.length,valor:it.reduce((s,l)=>s+(l.valor_risco||0),0),
      bruto:it.reduce((s,l)=>s+(l.qt||0)*(l.custo_unit||0),0)};});
  // filtro pelo gráfico/cards: clicar numa faixa filtra a tabela por aquela faixa de dias
  const Lf=S.valFaixa?L.filter(l=>l.dias_para_vencer>=S.valFaixa[0]&&l.dias_para_vencer<=S.valFaixa[1]):L;
  const baseCols=[C.red,C.orange,C.yellow,C.accent,C.dim];
  const barCols=baseCols.map((c,i)=>(!S.valFaixa||S.valFaixa[2]===faixas[i][0])?c:'rgba(100,116,139,.28)');
  const cards=fd.map((f,i)=>`<div class="vfx ${S.valFaixa&&S.valFaixa[2]===f.n?'on':''}" data-i="${i}" style="--c:${baseCols[i]}">
      <div class="vfx-h">${f.n} dias</div>
      <div class="vfx-v">${money(f.valor)}</div>
      <div class="vfx-s">risco · ${int(f.qt)} lotes · estoque ${moneyK(f.bruto)}</div></div>`).join('');
  // vencimento por comprador (respeita a faixa selecionada) — clicável p/ filtrar
  const compMap={}; (S.produtosAll||[]).forEach(p=>{if(p.comprador&&p.codcomprador!=null)compMap[p.comprador]=p.codcomprador;});
  const cg={}; Lf.forEach(l=>{const nome=l.comprador||'Sem comprador';const g=cg[nome]=cg[nome]||{nome,bruto:0,risco:0,n:0};
    g.bruto+=(l.qt||0)*(l.custo_unit||0); g.risco+=(l.valor_risco||0); g.n++;});
  const compRows=Object.values(cg).sort((a,b)=>b.bruto-a.bruto);
  const compTbl=`<h3>Vencimento por comprador</h3>
    <div class="tbl-wrap"><table><thead><tr><th>Comprador</th><th class="num">Estoque</th><th class="num">Risco</th><th class="num">Lotes</th></tr></thead>
    <tbody>${compRows.map(g=>{const cod=compMap[g.nome],sel=cod!=null&&String(cod)===S.cli.comprador;
      return `<tr data-comp="${cod!=null?cod:''}" style="${cod!=null?'cursor:pointer;':'opacity:.65;'}${sel?'background:var(--surface3);':''}"><td><span class="prod">${esc(g.nome)}</span></td><td class="num">${money(g.bruto)}</td><td class="num">${moneyK(g.risco)}</td><td class="num">${int(g.n)}</td></tr>`;}).join('')||'<tr><td colspan="4" class="muted">—</td></tr>'}</tbody></table></div>`;
  const el=$('#v-validade');
  el.innerHTML=head(`Validade / FEFO — próximos ${S.params.hor} dias`,'validade')
    +`<div class="panel"><h3>Por faixa de validade <small class="muted">· estoque parado vs. risco · clique p/ filtrar</small></h3>
        <div class="vfx-row">${cards}</div>
        <div class="row" style="align-items:flex-start">
          <div style="flex:0 0 340px;max-width:340px"><div class="chart-box sm" style="height:170px"><canvas id="ch-val"></canvas></div></div>
          <div class="grow">${compTbl}</div>
        </div></div>
      <div class="panel" id="val-tbl"></div>`;
  const Ld=S.cli.valDias?Lf.filter(l=>l.dias_para_vencer<=S.cli.valDias):Lf;
  $('#val-tbl').innerHTML=
    `<div class="row" style="gap:14px;margin:0 0 8px;align-items:flex-end">
       <div class="fb-group"><label>Dias para vencer (≤)</label><input type="number" id="val-dias" value="${S.cli.valDias||''}" min="0" step="5" placeholder="todos" style="width:120px"></div>
       ${S.valFaixa?`<div class="count-line" style="margin:0">Filtrando faixa <b>${S.valFaixa[2]} dias</b> · <a href="#" id="val-clear">limpar</a></div>`:''}
     </div>`
    +renderTableInline(Ld,cols,'validade');
  const vd=$('#val-dias'); if(vd) vd.onchange=e=>{ S.cli.valDias=e.target.value!==''?Math.max(0,+e.target.value):''; render(); };
  if(S.valFaixa){const c=$('#val-clear'); if(c)c.onclick=e=>{e.preventDefault();S.valFaixa=null;render();};}
  el.querySelectorAll('.vfx').forEach(d=>d.onclick=()=>{const i=+d.dataset.i,f=faixas[i];S.valFaixa=(S.valFaixa&&S.valFaixa[2]===f[0])?null:[f[1],f[2],f[0]];render();});
  el.querySelectorAll('tr[data-comp]').forEach(tr=>{const cod=tr.dataset.comp; if(!cod)return;
    tr.onclick=()=>{ S.cli.comprador=(S.cli.comprador===cod)?'':cod; const sel=$('#f-comprador'); if(sel){sel.value=S.cli.comprador; S.compradorNome=S.cli.comprador?(sel.selectedOptions[0]?.textContent||''):'';} render(); };});
  chart('ch-val',{type:'bar',data:{labels:fd.map(f=>f.n),datasets:[{data:fd.map(f=>f.valor),backgroundColor:barCols,borderRadius:6}]},options:{
    onClick:(ev,els)=>{if(!els||!els.length)return;const i=els[0].index,f=faixas[i];S.valFaixa=(S.valFaixa&&S.valFaixa[2]===f[0])?null:[f[1],f[2],f[0]];render();},
    plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>'risco '+money(c.raw)+' · '+fd[c.dataIndex].qt+' lotes'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
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

/* ───────── resumo de faixas reutilizável (cards + gráfico + por comprador) ───────── */
function porCompradorHTML(items,valorFn){
  const cg={}; items.forEach(p=>{const nome=p.comprador||'Sem comprador';const cod=p.codcomprador;
    const g=cg[nome]=cg[nome]||{nome,cod,valor:0,n:0}; g.valor+=valorFn(p)||0; g.n++;});
  const rows=Object.values(cg).sort((a,b)=>b.valor-a.valor);
  return `<h3>Por comprador</h3><div class="tbl-wrap"><table><thead><tr><th>Comprador</th><th class="num">Valor</th><th class="num">Itens</th></tr></thead>
    <tbody>${rows.map(g=>{const sel=g.cod!=null&&String(g.cod)===S.cli.comprador;
      return `<tr data-comp="${g.cod!=null?g.cod:''}" style="${g.cod!=null?'cursor:pointer;':'opacity:.65;'}${sel?'background:var(--surface3);':''}"><td><span class="prod">${esc(g.nome)}</span></td><td class="num">${money(g.valor)}</td><td class="num">${int(g.n)}</td></tr>`;}).join('')||'<tr><td colspan="3" class="muted">—</td></tr>'}</tbody></table></div>`;
}
function wirePorComprador(el){
  el.querySelectorAll('tr[data-comp]').forEach(tr=>{const cod=tr.dataset.comp; if(!cod)return;
    tr.onclick=()=>{S.cli.comprador=(S.cli.comprador===cod)?'':cod; const sel=$('#f-comprador'); if(sel){sel.value=S.cli.comprador;S.compradorNome=S.cli.comprador?(sel.selectedOptions[0]?.textContent||''):'';} render();};});
}
function resumoFaixasBlock(titulo,faixas,items,valorFn,active,chartId){
  const cards=faixas.map(f=>`<div class="vfx ${(Array.isArray(active)?active.includes(f.key):f.key===active)?'on':''}" data-fkey="${f.key}" style="--c:${f.color}">
      <div class="vfx-h">${f.label}</div><div class="vfx-v">${money(f.valor)}</div>
      <div class="vfx-s">${int(f.qt)} itens</div></div>`).join('');
  return `<div class="panel"><h3>${titulo} <small class="muted">· clique p/ filtrar</small></h3>
      <div class="vfx-row">${cards}</div>
      <div class="row" style="align-items:flex-start">
        <div style="flex:0 0 340px;max-width:340px"><div class="chart-box sm" style="height:170px"><canvas id="${chartId}"></canvas></div></div>
        <div class="grow">${porCompradorHTML(items,valorFn)}</div>
      </div></div>`;
}
function drawFaixaChart(id,faixas,onPick){
  chart(id,{type:'bar',data:{labels:faixas.map(f=>f.label),datasets:[{data:faixas.map(f=>f.valor),backgroundColor:faixas.map(f=>f.color),borderRadius:6}]},
    options:{
      onClick:(ev,els)=>{ if(onPick&&els&&els.length) onPick(faixas[els[0].index]); },
      onHover:(ev,els)=>{ if(ev.native) ev.native.target.style.cursor=(onPick&&els.length)?'pointer':'default'; },
      plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>money(c.raw)+' · '+faixas[c.dataIndex].qt+' itens'}}},scales:{y:{ticks:{callback:v=>moneyK(v)}}}}});
}

// "dias parados" = dias sem venda; nunca-vendeu (null) conta como o pior (infinito → cai em 121+)
function paradoDias(p){ return (p.dias_sem_venda==null) ? Infinity : p.dias_sem_venda; }
// faixas FIXAS do gráfico-indicador (partição inteira, ≥ início, sem gap nem sobreposição)
const FX_PARADO=[{label:'15-30',lo:15,hi:30,color:C.green},{label:'31-60',lo:31,hi:60,color:C.yellow},
  {label:'61-90',lo:61,hi:90,color:C.orange},{label:'91-120',lo:91,hi:120,color:C.red},
  {label:'121+',lo:121,hi:Infinity,color:C.purple}];
function paradoFaixaLabel(p){ const d=paradoDias(p); const f=FX_PARADO.find(f=>d>=f.lo&&d<=f.hi); return f?f.label:null; }
function setParMin(v){ S.params.parado=v; const i=$('#p-parado'); if(i)i.value=v; render(); savePrefs(); }

const parFaixaLabel=arr=>!arr.length?'Todas':(arr.length===1?arr[0]:`${arr.length} faixas`);
function parToggle(k){ const a=S.cli.parFaixa||[]; S.cli.parFaixa=a.includes(k)?a.filter(x=>x!==k):[...a,k]; render(); }
function renderParado(P){
  // universo do PARADO = itens com estoque e ≥15 dias sem venda (parado_faixa != null, nunca-vendeu
  // em 121+), partido nas faixas 15-30…121+. As faixas SOMAM o total (reconcilia como a Cobertura).
  const universo=P.filter(p=>p.parado_faixa);
  const faixas=FX_PARADO.map(f=>{const it=universo.filter(p=>p.parado_faixa===f.label);
    return{...f,key:f.label,valor:it.reduce((s,p)=>s+(p.valor||0),0),qt:it.length};});
  const pf=S.cli.parFaixa||[];
  const par=pf.length?universo.filter(p=>pf.includes(p.parado_faixa)):universo;
  const totItens=par.length, totVal=par.reduce((s,p)=>s+(p.valor||0),0);
  if(!S.sort.parado) S.sort.parado={key:'valor',dir:-1};   // maior valor parado primeiro
  P.forEach(p=>{const cx=p.caixa||1; p._dispCx=cx>1?Math.round((p.qtdisp||0)/cx):null;});
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'dtultsaida',label:'Última venda',fmt:v=>dt(v)},
    {key:'dias_sem_venda',label:'Dias parado',num:true,fmt:v=>v==null?'nunca':int(v)},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},
    {key:'_dispCx',label:'Disp. cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'valor',label:'Valor',num:true,fmt:money},
    {key:'status_saida',label:'Saída',badge:true},{key:'parado_faixa',label:'Faixa',badge:true},
    {key:'_plano',label:'Ação',html:p=>planoCell('parado',String(p.codprod),p.codprod,p.descricao,null)}];
  const el=$('#v-parado');
  el.innerHTML=head('Estoque parado — o que liquidar','parado')
    +resumoFaixasBlock('Valor parado por faixa (dias sem venda)',faixas,universo,p=>p.valor,pf,'ch-parado')
    +`<div class="row" style="gap:14px;margin:6px 0;align-items:flex-end">
        <div class="fb-group"><label>Faixa <small class="muted">(marque várias)</small></label>
          <details class="ms" id="par-faixa"><summary class="fb-control" style="width:auto">${parFaixaLabel(pf)}</summary>
            <div class="ms-menu">${FX_PARADO.map(f=>`<label><input type="checkbox" value="${f.label}" ${pf.includes(f.label)?'checked':''}>${f.label} dias</label>`).join('')}</div>
          </details></div>
        <div class="count-line" style="margin:0"><b>${int(totItens)} itens</b> · ${money(totVal)} parados${pf.length?' na(s) faixa(s) marcada(s)':' (≥15 dias, nunca vendidos incluídos)'}. <b>As faixas somam o total.</b> Clique num card/barra ou marque várias faixas.</div>
      </div>`
    +renderTable(par,cols,'parado');
  drawFaixaChart('ch-parado',faixas,f=>parToggle(f.key));
  el.querySelectorAll('.vfx[data-fkey]').forEach(d=>d.onclick=()=>parToggle(d.dataset.fkey));
  const fx=$('#par-faixa'); if(fx) fx.addEventListener('change',()=>{S.cli.parFaixa=[...fx.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value);render();});
  wirePorComprador(el);
  wirePlanoCells();
}

// zona de ação de cada célula ABC×XYZ: 1=automatizar(verde) 2=monitorar(âmbar) 3=sob demanda(vermelho)
const AXZONE={AX:1,AY:1,BX:1, AZ:2,BY:2,BZ:2,CX:2, CY:3,CZ:3};
function renderABCXYZ(P){
  const m={}; P.forEach(p=>{if(p.abc_xyz){(m[p.abc_xyz]=m[p.abc_xyz]||{qt:0,venda:0});m[p.abc_xyz].qt++;m[p.abc_xyz].venda+=(p.venda||0);}});
  const cell=(a,x)=>m[a+x]||{qt:0,venda:0};
  const rowT=a=>['X','Y','Z'].reduce((o,x)=>{const d=cell(a,x);return{qt:o.qt+d.qt,venda:o.venda+d.venda};},{qt:0,venda:0});
  const colT=x=>['A','B','C'].reduce((o,a)=>{const d=cell(a,x);return{qt:o.qt+d.qt,venda:o.venda+d.venda};},{qt:0,venda:0});
  const totVenda=['A','B','C'].reduce((s,a)=>s+rowT(a).venda,0)||1, totQt=P.length||1;
  // grid com totais nas margens
  let g=`<div class="axm"><div class="axm-corner"><span style="font-size:.58rem;color:var(--text-mute);text-transform:uppercase;letter-spacing:.5px;line-height:1.3">ABC↓<br>XYZ→</span></div>`+
    `<div class="axm-h"><b>X</b> estável</div><div class="axm-h"><b>Y</b> variável</div><div class="axm-h"><b>Z</b> errático</div><div class="axm-h">Total<br>curva</div>`;
  ['A','B','C'].forEach(a=>{
    g+=`<div class="axm-rh">${a}</div>`;
    ['X','Y','Z'].forEach(x=>{const k=a+x,d=cell(a,x),z=AXZONE[k];
      g+= d.qt
        ? `<div class="axm-cell z${z}" data-key="${k}" title="${k} · clique para listar os produtos"><span class="k">${k}</span><span class="p">${dec(d.venda/totVenda*100,0)}%</span><span class="q">${int(d.qt)}</span><span class="v">${moneyK(d.venda)}</span></div>`
        : `<div class="axm-cell empty"><span class="k">${k}</span><span class="q">0</span></div>`;});
    const rt=rowT(a);
    g+=`<div class="axm-tot"><span class="q">${int(rt.qt)}</span><span class="v">${moneyK(rt.venda)}</span></div>`;
  });
  g+=`<div class="axm-rh" style="font-size:.72rem;color:var(--text-mute)">Σ</div>`+
    ['X','Y','Z'].map(x=>{const ct=colT(x);return `<div class="axm-tot"><span class="q">${int(ct.qt)}</span><span class="v">${moneyK(ct.venda)}</span></div>`;}).join('')+
    `<div class="axm-tot grand"><span class="q">${int(totQt)}</span><span class="v">${moneyK(totVenda)}</span></div></div>`;
  // legenda por zona (mesma cor das células)
  const zones=[
    {z:'var(--green)',t:'Automatizar · nunca faltar',d:'Alto/médio valor e demanda previsível. Reposição no automático, controle rígido.',c:'AX · AY · BX'},
    {z:'var(--yellow)',t:'Monitorar · estoque de segurança',d:'Valor alto porém errático, ou giro baixo previsível. Acompanhar de perto e proteger com margem de segurança.',c:'AZ · BY · BZ · CX'},
    {z:'var(--red)',t:'Sob demanda · descontinuar',d:'Baixo valor e demanda imprevisível. Comprar sob pedido ou tirar de linha.',c:'CY · CZ'}];
  const leg=zones.map(o=>`<div class="axm-zone" style="--z:${o.z}"><div><div class="zt">${o.t}</div><div class="zd">${o.d}</div><div class="zc">${o.c}</div></div></div>`).join('');
  // leitura (insights automáticos)
  const redV=cell('C','Y').venda+cell('C','Z').venda, redQ=cell('C','Y').qt+cell('C','Z').qt, az=cell('A','Z'), cT=rowT('C');
  const read=`<div class="axm-read">
    <div class="ri"><span>Zona vermelha (CY+CZ) — candidatos a sair</span><b style="color:var(--red)">${moneyK(redV)} · ${int(redQ)} itens</b></div>
    <div class="ri"><span>AZ — alto valor, demanda errática (risco de ruptura)</span><b style="color:var(--yellow)">${int(az.qt)} itens · ${moneyK(az.venda)}</b></div>
    <div class="ri"><span>Curva C: ${dec(cT.qt/totQt*100,0)}% dos itens, só ${dec(cT.venda/totVenda*100,0)}% da venda</span><b>${int(cT.qt)} itens</b></div></div>`;
  $('#v-abcxyz').innerHTML=`<h2 class="section"><span>Matriz ABC-XYZ</span></h2>
    <div class="row">
      <div class="panel" style="flex:1.7 1 540px"><h3>Curva de vendas (ABC) × Variabilidade da demanda (XYZ)</h3>${g}
        <div class="count-line" style="margin-top:14px">Cor = zona de ação · número = itens · valor = <b>venda</b> do período · % = fatia da venda. Clique numa célula para listar os produtos.</div></div>
      <div class="panel grow" style="flex:1 1 300px"><h3>Estratégia por zona</h3>${leg}
        <h3 style="margin-top:18px">Leitura</h3>${read}</div>
    </div>`;
  $('#v-abcxyz').querySelectorAll('.axm-cell[data-key]').forEach(c=>c.onclick=()=>{const k=c.dataset.key;S.cli.curva=k[0];S.cli.xyz=k[1];$('#f-curva').value=k[0];$('#f-xyz').value=k[1];goView('produtos',{});});
}

function renderFornecedores(P){
  // Opção A: nesta aba o filtro "Curva" age pela ABC do FORNECEDOR (não do produto).
  // Agrega ignorando a curva do produto (filtered(true)) e filtra os fornecedores por ABC no fim.
  const base=filtered(true);
  const tv=base.reduce((s,p)=>s+(p.valor||0),0)||1,tvenda=base.reduce((s,p)=>s+(p.venda||0),0)||1,g={};
  base.forEach(p=>{if(p.codfornec==null)return;const o=g[p.codfornec]=g[p.codfornec]||{codfornec:p.codfornec,fornecedor:p.fornecedor||('FORN '+p.codfornec),n_produtos:0,valor:0,giro:0,venda:0,lucro:0,disp:0,girodia:0};o.n_produtos++;o.valor+=(p.valor||0);o.giro+=(p.giro_mes||0);o.venda+=(p.venda||0);o.lucro+=(p.lucro||0);o.disp+=(p.qtdisp||0);o.girodia+=(p.giro_dia||0);});
  const lead=S.params.lead||10;
  // índice = % na VENDA (R$) ÷ % no ESTOQUE (R$) — "vende mais do que pesa". Antes usava giro em
  // unidades, distorcendo fornecedor de alto valor/baixo volume.
  const F=Object.values(g).map(o=>{const pv=o.venda/tvenda*100,pe=o.valor/tv*100,idx=pe>0?pv/pe:(pv>0?999:0),cobertura=o.girodia>0?o.disp/o.girodia:null;
    let cl=(o.giro<=0&&o.venda<=0)?'critico_sem_giro':(cobertura!=null&&cobertura<lead?'ruptura':(idx>=1.2?'alta_performance':(idx>=0.8?'equilibrado':'estoque_alto')));
    return{...o,pv,pe,idx,cobertura,margem:o.venda?o.lucro/o.venda*100:null,cl};}).sort((a,b)=>b.valor-a.valor);
  // curva ABC do fornecedor por venda (Pareto do faturamento) — mesma leitura dos produtos
  {const _tv=F.reduce((s,o)=>s+(o.venda||0),0)||1; let _ac=0;
   [...F].sort((a,b)=>(b.venda||0)-(a.venda||0)).forEach(o=>{_ac+=(o.venda||0);const _p=_ac/_tv*100;o.curva_abc=_p<=80?'A':(_p<=95?'B':'C');});}
  const cols=[{key:'codfornec',label:'Cód',num:true},{key:'fornecedor',label:'Fornecedor',fmt:v=>`<span class="prod">${esc(v)}</span>`},
    {key:'curva_abc',label:'ABC',badge:true},
    {key:'n_produtos',label:'Itens',num:true},{key:'valor',label:'Estoque',num:true,fmt:money},{key:'giro',label:'Giro/mês',num:true,fmt:int},
    {key:'cobertura',label:'Cob.',num:true,fmt:cob},
    {key:'venda',label:'Venda',num:true,fmt:money},{key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
    {key:'pe',label:'% est.',num:true,fmt:v=>dec(v,1)+'%'},{key:'pv',label:'% venda',num:true,fmt:v=>dec(v,1)+'%'},
    {key:'idx',label:'Índice',num:true,fmt:v=>dec(v,2)},{key:'cl',label:'Classe',badge:true}];
  const CLS={alta_performance:'Alta performance',equilibrado:'Equilibrado',estoque_alto:'Estoque alto',ruptura:'Ruptura',critico_sem_giro:'Crítico s/ giro'};
  const Fabc=S.cli.curva?F.filter(r=>r.curva_abc===S.cli.curva):F;   // filtro Curva = ABC do fornecedor
  const Ff=S.cli.fornClasse?Fabc.filter(r=>r.cl===S.cli.fornClasse):Fabc;
  const sk=S.sort['fornecedores']||{key:'valor',dir:-1};
  const rows=[...Ff].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(typeof x==='string')return sk.dir*x.localeCompare(y);return sk.dir*((x||0)-(y||0));});
  const headr=cols.map(c=>`<th class="${c.num?'num':''}" data-k="${c.key}">${c.label}</th>`).join('');
  const body=rows.slice(0,300).map(r=>'<tr>'+cols.map(c=>{let v=r[c.key];if(c.badge)return`<td>${badge(v)}</td>`;if(c.fmt)v=c.fmt(v);return`<td class="${c.num?'num':''}">${v==null?'—':v}</td>`;}).join('')+'</tr>').join('');
  $('#v-fornecedores').innerHTML=head('Desempenho por fornecedor — giro × estoque','fornecedores')+
    `<div class="fb-group" style="margin:0 0 6px"><label>Filtrar classe</label>
       <select id="forn-cl" class="fb-control" style="width:auto">
         <option value="">Todas</option>
         ${Object.keys(CLS).map(k=>`<option value="${k}" ${S.cli.fornClasse===k?'selected':''}>${CLS[k]}</option>`).join('')}
       </select></div>
     <div class="count-line">Índice = % na <b>venda (R$)</b> ÷ % no <b>estoque (R$)</b> (&gt;1 = vende mais do que pesa em estoque). <b>Ruptura</b> = vende mas cobertura &lt; ${lead}d (quase sem estoque) — não é performance.</div><div class="tbl-wrap"><table><thead><tr>${headr}</tr></thead><tbody>${body}</tbody></table></div>`;
  $('#v-fornecedores').querySelectorAll('thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort['fornecedores']||{};S.sort['fornecedores']={key:k,dir:cur.key===k?-cur.dir:-1};render();});
  const fc=$('#forn-cl'); if(fc) fc.onchange=e=>{S.cli.fornClasse=e.target.value;render();};
}

function renderRupturaComprador(P){
  // agrega métricas de ruptura por uma chave (comprador OU curva ABC de venda)
  function agrupa(keyFn,nomeFn){
    const g={};
    P.forEach(p=>{const kk=keyFn(p); const o=g[kk]=g[kk]||{k:kk,nome:nomeFn(p,kk),n:0,rupt:0,semped:0,perdida:0,repor:0,diasSum:0,diasN:0};
      o.n++;
      if((p.qtdisp||0)<=0&&(p.giro_dia||0)>0){o.rupt++; if((p.qtd_ja_pedida||0)<=0)o.semped++;
        o.perdida+=(p.venda_perdida||0); o.repor+=(p.sugestao_compra||0)*(p.custo_unit||0);
        if(p.dias_sem_venda!=null){o.diasSum+=p.dias_sem_venda; o.diasN++;}}});
    return Object.values(g).map(o=>({...o,pct:o.n?o.rupt/o.n*100:0,diasrup:o.diasN?Math.round(o.diasSum/o.diasN):0})).filter(o=>o.n>0);
  }
  const ckBase=[{k:'n',label:'Produtos',num:1},{k:'rupt',label:'Em ruptura',num:1},{k:'pct',label:'% Rupt.',num:1},
    {k:'diasrup',label:'Dias rupt. méd',num:1},{k:'semped',label:'Sem pedido',num:1},{k:'perdida',label:'Venda perdida',num:1},{k:'repor',label:'Custo reposição',num:1}];
  function tabela(rows0,skk,lbl0,nav){
    const sk=S.sort[skk]||{key:'rupt',dir:-1};
    const rows=_sortArr(rows0,sk);
    const ck=[{k:'nome',label:lbl0},...ckBase];
    const T=rows.reduce((s,r)=>({n:s.n+r.n,rupt:s.rupt+r.rupt,semped:s.semped+r.semped,perdida:s.perdida+r.perdida,repor:s.repor+r.repor,diasSum:s.diasSum+r.diasSum,diasN:s.diasN+r.diasN}),{n:0,rupt:0,semped:0,perdida:0,repor:0,diasSum:0,diasN:0});
    const totRow=rows.length?`<tr style="border-top:2px solid var(--border);font-weight:700"><td>TOTAL</td><td class="num">${int(T.n)}</td><td class="num">${int(T.rupt)}</td><td class="num">${T.n?dec(T.rupt/T.n*100,1):'0'}%</td><td class="num">${T.diasN?int(Math.round(T.diasSum/T.diasN))+'d':'—'}</td><td class="num">${int(T.semped)}</td><td class="num">${money(T.perdida)}</td><td class="num">${money(T.repor)}</td></tr>`:'';
    return `<div class="tbl-wrap"><table><thead><tr>${ck.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('')}</tr></thead>
      <tbody>${rows.map(r=>`<tr${nav?` data-curva="${esc(r.k)}" style="cursor:pointer"`:''}><td><span class="prod">${esc(r.nome)}</span></td><td class="num">${int(r.n)}</td><td class="num">${int(r.rupt)}</td><td class="num">${dec(r.pct,1)}%</td><td class="num">${r.diasrup?int(r.diasrup)+'d':'—'}</td><td class="num">${int(r.semped)}</td><td class="num">${money(r.perdida)}</td><td class="num">${money(r.repor)}</td></tr>`).join('')||'<tr><td colspan="8" class="muted">Sem ruptura 🎉</td></tr>'}${totRow}</tbody></table></div>`;
  }
  const porComp=agrupa(p=>p.codcomprador==null?0:p.codcomprador, p=>p.comprador||'Sem comprador');
  const porCurva=agrupa(p=>p.curva_abc||'C', (p,k)=>'Curva '+k);
  const totR=porComp.reduce((s,r)=>s+r.rupt,0),totSem=porComp.reduce((s,r)=>s+r.semped,0),
    totP=porComp.reduce((s,r)=>s+r.perdida,0),totC=porComp.reduce((s,r)=>s+r.repor,0);
  $('#v-ruptura_comprador').innerHTML=head('Ruptura por comprador','ruptura_comprador')+
    `<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
       ${kpi('Itens em ruptura',int(totR),int(totSem)+' sem pedido',C.red)}
       ${kpi('Venda perdida (ruptura)',money(totP),'acumulada · a preço de venda',C.orange)}
       ${kpi('Custo de reposição',money(totC),'p/ atingir o alvo',C.accent)}
       ${kpi('Compradores',int(porComp.length),'',C.accent2)}
     </div>
     <div class="count-line">Ruptura = estoque ≤ 0 e giro > 0. <b>"Dias rupt. méd"</b> = média de dias sem venda dos itens em ruptura (há quanto tempo, em média, estão zerados). "Sem pedido" = ainda sem pedido de compra em aberto (risco real). <b>"Venda perdida"</b> = dias em ruptura (desde a última venda, teto 60d) × giro/dia × <b>preço de venda</b> (realizado 3m) — o que se deixou de vender no período parado. "Custo reposição" = sugestão × <b>custo</b> (o que falta comprar até o alvo).</div>
     <div class="panel" id="rc-comp"><h3>Por comprador</h3>${tabela(porComp,'ruptcomp','Comprador')}</div>
     <div class="panel" id="rc-curva"><h3>Por curva ABC <small class="muted">· quanto da ruptura está em cada curva de venda (A = campeões) · clique p/ ver os itens</small></h3>${tabela(porCurva,'ruptcurva','Curva ABC',true)}</div>`;
  wireSortTbl($('#rc-comp'),'ruptcomp',render);
  wireSortTbl($('#rc-curva'),'ruptcurva',render);
  $('#rc-curva').querySelectorAll('tr[data-curva]').forEach(tr=>tr.onclick=()=>goView('estoque_zero',{curva:tr.dataset.curva}));
}

function renderProdutos(P){
  // colunas em caixa (mantém unidade e ACRESCENTA cx) — fator un/cx de cada item
  P.forEach(p=>{const cx=p.caixa||1; p._giroCx=cx>1?Math.round((p.giro_mes||0)/cx):(p.giro_mes||0); p._dispCx=cx>1?Math.round((p.qtdisp||0)/cx):(p.qtdisp||0);});
  const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'xyz',label:'XYZ',badge:true},
    {key:'qtdisp',label:'Disp.',num:true,fmt:int},{key:'_dispCx',label:'Disp. cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'qtbloq',label:'Avaria',num:true,fmt:v=>v?int(v):'—'},
    {key:'qtd_ja_pedida',label:'Já ped.',num:true,fmt:v=>v>0?int(v):'—'},
    colGiroSpark,{key:'_giroCx',label:'Giro cx',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'cobertura',label:'Cob.',num:true,fmt:cob},
    {key:'dias_sem_venda',label:'Dias s/v',num:true,fmt:v=>v==null?'—':int(v)},
    {key:'venda',label:'Venda',num:true,fmt:money},{key:'lucro',label:'Lucro',num:true,fmt:money},
    {key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
    {key:'valor',label:'Estoque R$',num:true,fmt:money},{key:'status_abast',label:'Abast.',badge:true}];
  // filtros LOCAIS desta aba (multi-seleção) — não afetam as outras abas
  const abn=S.cli.abast||[], mgn=S.cli.margem||[];
  let rows=P;
  if(abn.length) rows=rows.filter(p=>abn.includes(p.status_abast));
  if(mgn.length) rows=rows.filter(p=>mgn.includes(margemBucket(p)));
  const abastCtl=`<div class="fb-group" style="margin:0"><label>Abastecimento</label>
      <details class="ms" id="pr-abast"><summary class="fb-control">${abastLabel(abn)}</summary>
        <div class="ms-menu">${Object.entries(ABAST_LABELS).map(([v,l])=>`<label><input type="checkbox" value="${v}" ${abn.includes(v)?'checked':''}>${l}</label>`).join('')}</div>
      </details></div>`;
  const margemCtl=`<div class="fb-group" style="margin:0"><label>Margem</label>
      <details class="ms" id="pr-margem"><summary class="fb-control">${margemLabel(mgn)}</summary>
        <div class="ms-menu">${Object.entries(MARGEM_LABELS).map(([v,l])=>`<label><input type="checkbox" value="${v}" ${mgn.includes(v)?'checked':''}>${l}</label>`).join('')}</div>
      </details></div>`;
  $('#v-produtos').innerHTML=head('Explorador de produtos','produtos')+`<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px">${abastCtl}${margemCtl}</div>`+renderTable(rows,cols,'produtos');
  const d=$('#pr-abast');
  if(d) d.addEventListener('change',()=>{ S.cli.abast=[...d.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value); render(); });
  const dm=$('#pr-margem');
  if(dm) dm.addEventListener('change',()=>{ S.cli.margem=[...dm.querySelectorAll('input[type=checkbox]:checked')].map(c=>c.value); render(); });
}

const STAT_LUCRO={alta:['Alta entrega',C.green],boa:['Boa entrega',C.accent],baixa:['Entrega baixa',C.dim],negativo:['Lucro negativo',C.red]};
async function renderDesempenho(){
  const el=$('#v-desempenho');
  el.innerHTML=`<div class="loader"><div class="spinner"></div>Carregando desempenho comercial…</div>`;
  let j; try{ j=await getJSON('/api/desempenho?venda_periodo='+encodeURIComponent(S.vperiodo)); }
  catch(e){ el.innerHTML=`<div class="empty">Falha ao carregar desempenho: ${esc(e.message)}</div>`; return; }
  let rows=j.compradores||[];
  // filtro de comprador do topo
  if(S.cli.comprador) rows=rows.filter(p=>String(p.codcomprador)===S.cli.comprador);
  // resumo (cards) recalculado a partir das linhas visíveis → respeita o filtro de comprador.
  // Sem filtro, bate igual ao resumo do servidor (soma das mesmas linhas).
  const _sum=k=>rows.reduce((a,p)=>a+(+p[k]||0),0);
  const _tv=_sum('venda_liquida'), _tl=_sum('lucro_bruto');
  const r={ venda_liquida:_tv, lucro_bruto:_tl, margem:_tv?(_tl/_tv*100):null,
            clientes_pos:_sum('clientes_pos'), devolucao:_sum('devolucao') };
  const perLbl=({mes:'mês atual',['90d']:'últimos 90d',['6m']:'6 meses',['12m']:'12 meses'})[S.vperiodo]||'período';
  const ck=[{k:'ranking',label:'#',num:1},{k:'comprador',label:'Comprador'},{k:'fornecedores',label:'Fornec.',num:1},
    {k:'clientes_pos',label:'Positivação',num:1},{k:'venda_liquida',label:'Venda líq.',num:1},{k:'lucro_bruto',label:'Lucro bruto',num:1},
    {k:'margem',label:'Margem',num:1},{k:'devolucao',label:'Devolução',num:1},{k:'part_lucro',label:'% Lucro',num:1},
    {k:'yoy',label:'AA Venda',num:1},{k:'yoy_lucro',label:'AA Lucro',num:1},{k:'status_lucro',label:'Status'}];
  const sk=S.sort['desempenho']||{key:'lucro_bruto',dir:-1};
  rows=[...rows].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=-Infinity;if(y==null)y=-Infinity;
    if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y));return sk.dir*(x-y);});
  const yoyCell=v=>v==null?'<span class="muted">—</span>':`<span style="color:${v>=0?C.green:C.red}">${v>=0?'+':''}${dec(v,1)}%</span>`;
  const statCell=v=>{const s=STAT_LUCRO[v];return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—';};
  el.innerHTML=`<h2 class="section"><span>Desempenho comercial por comprador</span>${exportBtns('desempenho')}</h2>
    <div class="count-line">Receita/lucro/positivação dos últimos <b>${perLbl}</b> (venda líquida = bruta − devoluções) · ano×ano = vs. mesmo período do ano anterior. Espelha a aba RECEITA COMPRADOR da planilha.</div>
    <div class="kpi-grid" style="grid-template-columns:repeat(5,1fr)">
      ${kpi('Venda líquida',money(r.venda_liquida),'',C.green)}
      ${kpi('Lucro bruto',money(r.lucro_bruto),'',C.accent2)}
      ${kpi('Margem',r.margem!=null?dec(r.margem,1)+'%':'—','',C.purple)}
      ${kpi('Positivação',int(r.clientes_pos),'clientes distintos',C.accent)}
      ${kpi('Devolução',money(r.devolucao),'',C.red)}</div>
    <div class="tbl-wrap"><table><thead><tr>${ck.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(p=>`<tr><td class="num">${int(p.ranking)}</td><td><span class="prod">${esc(p.comprador)}</span></td><td class="num">${int(p.fornecedores)}</td><td class="num">${int(p.clientes_pos)}</td><td class="num">${money(p.venda_liquida)}</td><td class="num">${money(p.lucro_bruto)}</td><td class="num">${p.margem==null?'—':dec(p.margem,1)+'%'}</td><td class="num">${money(p.devolucao)}</td><td class="num">${dec(p.part_lucro||0,1)}%</td><td class="num">${yoyCell(p.yoy)}</td><td class="num">${yoyCell(p.yoy_lucro)}</td><td>${statCell(p.status_lucro)}</td></tr>`).join('')||'<tr><td colspan="12" class="muted">Sem dados de venda no período.</td></tr>'}</tbody></table></div>
    <div class="count-line">${rows.length} compradores · positivação = clientes distintos atendidos no período (DISTINCTCOUNT cliente).</div>`;
  el.querySelectorAll('thead th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort['desempenho']||{};S.sort['desempenho']={key:k,dir:cur.key===k?-cur.dir:-1};render();});
}

function renderComprasVendas(P){
  const dim=S.cvDim, el=$('#v-comprasvendas');
  const seg=`<div class="seg" id="cv-seg">
    ${['comprador','fornecedor','produto'].map(d=>`<span class="seg-opt ${d===dim?'on':''}" data-d="${d}">${({comprador:'Por comprador',fornecedor:'Por fornecedor',produto:'Por produto'})[d]}</span>`).join('')}</div>`;
  const expv=dim==='comprador'?'compradores':(dim==='fornecedor'?'fornecedores':'comprasvendas');
  let html=`<h2 class="section"><span>Compras × Vendas — ${({comprador:'por comprador',fornecedor:'por fornecedor',produto:'por produto'})[dim]}</span>${exportBtns(expv)}</h2>
    <div class="count-line" style="display:flex;justify-content:space-between;align-items:center">${seg}<span>Estoque = capital em compras · Venda/Lucro/Margem = realizado no período (${({mes:'mês',['90d']:'90d',['6m']:'6m',['12m']:'12m'})[S.vperiodo]})</span></div>`;
  if(dim==='produto'){
    const cols=[colCod,colProd,colForn,{key:'curva_abc',label:'ABC',badge:true},{key:'comprador',label:'Comprador',fmt:v=>esc((v||'').split(' ')[0]||'—')},
      {key:'valor',label:'Estoque R$',num:true,fmt:money},{key:'venda',label:'Venda R$',num:true,fmt:money},
      {key:'lucro',label:'Lucro R$',num:true,fmt:money},{key:'margem',label:'Margem',num:true,fmt:v=>v==null?'—':dec(v,1)+'%'},
      colGiroSpark,{key:'cobertura',label:'Cob.',num:true,fmt:cob}];
    html+=renderTable(P,cols,'comprasvendas');
    el.innerHTML=html;
  } else {
    const base=dim==='fornecedor'?filtered(true):P;   // Opção A: em "por fornecedor" a Curva filtra pela ABC do fornecedor
    const g={};
    base.forEach(p=>{const key=dim==='fornecedor'?p.codfornec:p.codcomprador; if(key==null)return;
      const nome=dim==='fornecedor'?(p.fornecedor||'Forn '+key):(p.comprador||'Sem comprador');
      const o=g[key]=g[key]||{key,nome,n:0,estoque:0,venda:0,lucro:0,giro:0,rupt:0,parado:0};
      o.n++; o.estoque+=(p.valor||0); o.venda+=(p.venda||0); o.lucro+=(p.lucro||0); o.giro+=(p.giro_mes||0);
      // ruptura = critério oficial (estoque<=0 & giro>0); cobertura baixa é atenção, não ruptura
      if((p.qtdisp<=0)&&(p.giro_dia>0))o.rupt++; if(p.status_parado)o.parado+=(p.valor||0);});
    const rows0=Object.values(g).map(o=>({...o,margem:o.venda?o.lucro/o.venda*100:null,turn:o.estoque?o.venda/o.estoque:null,pct_rupt:o.n?o.rupt/o.n*100:0}));
    if(dim==='fornecedor'){const _tv=rows0.reduce((s,o)=>s+(o.venda||0),0)||1;let _ac=0;   // curva ABC do fornecedor por venda
      [...rows0].sort((a,b)=>(b.venda||0)-(a.venda||0)).forEach(o=>{_ac+=(o.venda||0);const _p=_ac/_tv*100;o.curva_abc=_p<=80?'A':(_p<=95?'B':'C');});}
    const ck=[{k:'nome',label:dim==='fornecedor'?'Fornecedor':'Comprador'},...(dim==='fornecedor'?[{k:'curva_abc',label:'ABC',badge:1}]:[]),{k:'n',label:'Itens',num:1},
      {k:'estoque',label:'Estoque R$',num:1},{k:'venda',label:'Venda R$',num:1},{k:'lucro',label:'Lucro R$',num:1},
      {k:'margem',label:'Margem',num:1},{k:'turn',label:'Venda/Estoque',num:1},{k:'rupt',label:'Ruptura',num:1},{k:'pct_rupt',label:'% Rupt.',num:1},{k:'parado',label:'Parado R$',num:1}];
    const skk='cv_'+dim, sk=S.sort[skk]||{key:'venda',dir:-1};
    const rows0f=(dim==='fornecedor'&&S.cli.curva)?rows0.filter(o=>o.curva_abc===S.cli.curva):rows0;
    const rows=[...rows0f].sort((a,b)=>{let x=a[sk.key],y=b[sk.key];if(x==null)x=-Infinity;if(y==null)y=-Infinity;
      if(typeof x==='string'||typeof y==='string')return sk.dir*String(x).localeCompare(String(y));return sk.dir*(x-y);});
    const totE=rows.reduce((s,r)=>s+r.estoque,0),totV=rows.reduce((s,r)=>s+r.venda,0),totL=rows.reduce((s,r)=>s+r.lucro,0);
    html+=`<div class="kpi-grid" style="grid-template-columns:repeat(4,1fr)">
      ${kpi('Estoque (compras)',money(totE),'',C.accent)}${kpi('Venda',money(totV),'',C.green)}
      ${kpi('Lucro',money(totL),'',C.accent2)}${kpi('Margem',totV?dec(totL/totV*100,1)+'%':'—','',C.purple)}</div>`;
    html+=`<div class="tbl-wrap"><table><thead><tr>${ck.map(c=>`<th class="${c.num?'num':''}" data-k="${c.k}">${c.label}${sk.key===c.k?(sk.dir<0?' ↓':' ↑'):''}</th>`).join('')}</tr></thead><tbody>`+
      rows.map(r=>`<tr><td><span class="prod">${esc(r.nome)}</span></td>${dim==='fornecedor'?`<td>${badge(r.curva_abc)}</td>`:''}<td class="num">${int(r.n)}</td><td class="num">${money(r.estoque)}</td><td class="num">${money(r.venda)}</td><td class="num">${money(r.lucro)}</td><td class="num">${r.margem==null?'—':dec(r.margem,1)+'%'}</td><td class="num">${r.turn==null?'—':dec(r.turn,2)+'×'}</td><td class="num">${int(r.rupt)}</td><td class="num">${dec(r.pct_rupt||0,1)}%</td><td class="num">${money(r.parado)}</td></tr>`).join('')+
      `</tbody></table></div><div class="count-line">${rows.length} ${dim==='fornecedor'?'fornecedores':'compradores'} · "Venda/Estoque" = quantas vezes o capital girou no período.</div>`;
    el.innerHTML=html;
    el.querySelectorAll('thead th[data-k]').forEach(th=>th.onclick=()=>{const k=th.dataset.k,cur=S.sort[skk]||{};S.sort[skk]={key:k,dir:cur.key===k?-cur.dir:-1};render();});
  }
  el.querySelectorAll('#cv-seg .seg-opt').forEach(o=>o.onclick=()=>{S.cvDim=o.dataset.d;render();});
  el.querySelectorAll('tbody tr[data-cod]').forEach(tr=>tr.onclick=()=>openProduto(tr.dataset.cod));
}

/* ───────── Orçamento ───────── */
const PRAZO_BADGE={atrasado:['Atrasado','#ef4444'],chega_7:['Chega ≤7d','#f97316'],no_prazo:['No prazo','#22c55e'],recebido:['Recebido','#22c55e'],sem_prev:['Sem previsão','#64748b']};
const prazoBadge=v=>{const s=PRAZO_BADGE[v];return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—';};

async function renderOrcamento(useCache){
  const el=$('#v-orcamento');
  const comp=S.compradorNome||'TODOS';
  let o=useCache?S.orcamento:null;
  if(!o){ el.innerHTML=`<div class="loader"><div class="spinner"></div></div>`;
    try{ o=await getJSON('/api/orcamento?comprador='+encodeURIComponent(comp)); }
    catch(e){ el.innerHTML=`<div class="empty">Orçamento indisponível: ${e.message}</div>`; return; }
    S.orcamento=o; }
  const r=o.resumo;
  const prog=r.pct_consumido!=null?Math.min(100,r.pct_consumido*100):0;
  const cor=prog>=100?C.red:(prog>=85?C.orange:C.green);
  const abertos=o.abertos||[], manuais=o.manuais||[];
  // ordenação clicável (mantém a ordem do servidor até o 1º clique)
  const skC=S.sort['orc_comp'], pcS=skC?_sortArr(o.por_comprador||[],skC):(o.por_comprador||[]);
  const colsC=[{k:'comprador',label:'Comprador'},{k:'meta',label:'Meta',num:1},{k:'comprado',label:'Comprado',num:1},{k:'aberto',label:'Aberto',num:1},{k:'saldo',label:'Saldo',num:1},{k:'pct_consumido',label:'Consumido',num:1}];
  const skA=S.sort['orc_abertos'], abertosS=skA?_sortArr(abertos,skA):abertos;
  const colsA=[{k:'numped',label:'Nº',num:1},{k:'data_pedido',label:'Data'},{k:'fornecedor',label:'Fornecedor'},{k:'comprador',label:'Comprador'},{k:'valor',label:'Valor',num:1},{k:'valor_aberto',label:'A entregar',num:1},{k:'dias_para_chegar',label:'Previsão entrega'},{k:'status_prazo',label:'Status'}];
  const skM=S.sort['orc_manuais'], manuaisS=skM?_sortArr(manuais,skM):manuais;
  const colsM=[{k:'data_pedido',label:'Data'},{k:'fornecedor',label:'Fornecedor'},{k:'n_pedido',label:'Pedido'},{k:'valor',label:'Valor',num:1}];
  el.innerHTML=`<h2 class="section"><span>Orçamento de compras — ${esc(comp)} · ${r.mes}</span>
      <span><button class="btn sm primary" id="btn-pedido">+ Pedido</button></span></h2>
    <div class="kpi-grid">
      ${kpi('Meta do mês',money(r.meta),r.meta_auto?'65% da venda líq. 30d':'meta manual',C.accent)}
      ${kpi('Comprado (Winthor)',money(r.comprado),r.n_pedidos+' pedidos',C.accent2)}
      ${kpi('Saldo',money(r.saldo),'comprometido aberto '+moneyK(r.aberto),r.saldo<0?C.red:C.green)}
      ${kpi('Consumido',r.pct_consumido!=null?pct(r.pct_consumido):'—','',cor)}
    </div>
    <div class="panel"><div class="bar big"><i style="width:${prog}%;background:${cor}"></i></div>
      <div class="count-line">${prog>=100?'⚠️ Meta estourada':(prog>=85?'Atenção: perto da meta':'Dentro do planejado')} · realizado lido direto do Winthor (pedido real).</div></div>
    ${pcS.length?`<div class="panel" id="orc-comp"><h3>Orçamento por comprador <small class="muted">· meta = 65% da venda líq. 30d por comprador</small></h3>
      <div class="tbl-wrap"><table><thead><tr>${sortTh(colsC,skC||{})}</tr></thead>
      <tbody>${pcS.map(c=>`<tr><td><span class="prod">${esc(c.comprador)}</span></td><td class="num">${money(c.meta)}</td><td class="num">${money(c.comprado)}</td><td class="num">${money(c.aberto)}</td><td class="num" style="color:${c.saldo<0?C.red:C.green}">${money(c.saldo)}</td><td class="num">${c.pct_consumido!=null?pct(c.pct_consumido):'—'}</td></tr>`).join('')}</tbody></table></div></div>`:''}
    ${(r.n_atrasados||r.n_chega7)?`<div class="alerts">
      ${r.n_atrasados?alertCard(r.n_atrasados,'Entregas atrasadas',sum2(abertos.filter(p=>p.status_prazo==='atrasado'),'valor_aberto'),C.red,'orcamento',{}):''}
      ${r.n_chega7?alertCard(r.n_chega7,'Chegam em ≤7 dias',sum2(abertos.filter(p=>p.status_prazo==='chega_7'),'valor_aberto'),C.orange,'orcamento',{}):''}
    </div>`:''}
    <div class="panel" id="orc-abertos"><h3>Acompanhamento de pedidos em aberto <small class="muted">· ${abertos.length} em aberto · ${moneyK(r.valor_aberto)} a entregar</small></h3>
      ${abertos.length?`<div class="tbl-wrap"><table><thead><tr>${sortTh(colsA,skA||{})}</tr></thead>
      <tbody>${abertosS.map(pe=>`<tr data-numped="${pe.numped}" style="cursor:pointer" title="ver itens comprados"><td class="num">${pe.numped}</td><td>${dt(pe.data_pedido)}</td><td><span class="prod">${esc(pe.fornecedor||'')}</span></td><td>${esc((pe.comprador||'').split(' ')[0]||'—')}</td><td class="num">${money(pe.valor)}</td><td class="num">${money(pe.valor_aberto)}</td><td>${dt(pe.dt_previsao)}${pe.dias_para_chegar!=null?` <small class="muted">(${pe.dias_para_chegar}d)</small>`:''}</td><td>${prazoBadge(pe.status_prazo)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Nenhum pedido em aberto.</div>'}
    </div>
    ${manuais.length?`<div class="panel" id="orc-manuais" style="border-color:var(--accent2)"><h3>Pedidos da nossa plataforma <small class="muted">· pendentes de envio ao Winthor</small></h3>
      <div class="tbl-wrap"><table><thead><tr>${sortTh(colsM,skM||{})}<th></th></tr></thead>
      <tbody>${manuaisS.map(pe=>`<tr><td>${dt(pe.data_pedido)}</td><td><span class="prod">${esc(pe.fornecedor||'')}</span></td><td>${esc(pe.n_pedido||'')}</td><td class="num">${money(+pe.valor)}</td><td><a class="btn sm" href="/api/pedidos/${pe.id}.pdf">⬇ PDF</a> <button class="btn sm" data-delped="${pe.id}">✕</button></td></tr>`).join('')}</tbody></table></div>
      <div class="count-line">Não somam no realizado — entram quando voltarem da base oficial (Winthor).</div></div>`:''}`;
  $('#btn-pedido').onclick=()=>modalPedido(null);
  wireSortTbl($('#orc-comp'),'orc_comp',()=>renderOrcamento(true));
  wireSortTbl($('#orc-abertos'),'orc_abertos',()=>renderOrcamento(true));
  wireSortTbl($('#orc-manuais'),'orc_manuais',()=>renderOrcamento(true));
  $('#orc-abertos').querySelectorAll('tr[data-numped]').forEach(tr=>tr.onclick=()=>modalPedidoItens(tr.dataset.numped));
  el.querySelectorAll('[data-delped]').forEach(b=>b.onclick=async()=>{ await postJSON('/api/pedidos/'+b.dataset.delped,{}, 'DELETE'); toast('Pedido removido'); renderOrcamento(); });
}
function sum2(arr,key){ key=key||'valor'; return arr.reduce((s,p)=>s+(p[key]||0),0); }

const RESUMO_COR={'URGENTE':'red','ALTO':'orange','ATENCAO':'yellow','BAIXO':'accent','OK':'green','RISCO RUPTURA':'red','CRITICO':'purple'};
const resumoBadge=s=>{const cor=C[RESUMO_COR[s]||'dim'];return `<span class="badge" style="background:${cor}22;color:${cor}">${s}</span>`;};
function resumoTabela(titulo,faixas,total,colQt,lblQt){
  return `<div class="panel grow"><h3>${titulo}</h3>
    <div class="tbl-wrap"><table><thead><tr><th>Faixa</th><th class="num">${lblQt}</th><th class="num">Valor estoque</th><th class="num">% ${lblQt.toLowerCase()}</th><th>Status</th></tr></thead>
    <tbody>${faixas.map(f=>`<tr><td>${f.faixa}</td><td class="num">${int(f[colQt])}</td><td class="num">${money(f.valor)}</td><td class="num">${pct(f.perc)}</td><td>${resumoBadge(f.status)}</td></tr>`).join('')}
    <tr style="border-top:2px solid var(--border);font-weight:700"><td>TOTAL</td><td class="num">${int(total[colQt])}</td><td class="num">${money(total.valor)}</td><td class="num">100%</td><td></td></tr></tbody></table></div></div>`;
}
// variante da resumoTabela p/ um SUBGRUPO de faixas: calcula o próprio subtotal
// (itens/valor/%) — usada p/ separar Cobertura de Estoque × Estoque Parado.
function resumoTabelaGrupo(titulo,faixas,colQt,lblQt){
  const tQt=faixas.reduce((s,f)=>s+(f[colQt]||0),0),tVal=faixas.reduce((s,f)=>s+(f.valor||0),0),tPerc=faixas.reduce((s,f)=>s+(f.perc||0),0);
  return `<div class="panel grow"><h3>${titulo}</h3>
    <div class="tbl-wrap"><table><thead><tr><th>Faixa</th><th class="num">${lblQt}</th><th class="num">Valor estoque</th><th class="num">% ${lblQt.toLowerCase()}</th><th>Status</th></tr></thead>
    <tbody>${faixas.map(f=>`<tr><td>${f.faixa}</td><td class="num">${int(f[colQt])}</td><td class="num">${money(f.valor)}</td><td class="num">${pct(f.perc)}</td><td>${resumoBadge(f.status)}</td></tr>`).join('')}
    <tr style="border-top:2px solid var(--border);font-weight:700"><td>TOTAL</td><td class="num">${int(tQt)}</td><td class="num">${money(tVal)}</td><td class="num">${pct(tPerc)}</td><td></td></tr></tbody></table></div></div>`;
}
function resumoCard(titulo,rows,cor){
  return `<div class="panel grow"><h3>${titulo}</h3>
    <table class="mini">${rows.map(([l,v])=>`<tr><td class="muted">${l}</td><td class="num"><b>${v}</b></td></tr>`).join('')}</table>
    ${cor?`<div class="bar" style="margin-top:8px"><i style="width:0;background:${cor}"></i></div>`:''}</div>`;
}
async function injectResumos(sel){
  const el=$(sel); if(!el) return;
  const comp=S.compradorNome||'TODOS';
  let o; try{ o=await getJSON('/api/resumos?'+serverQS()+'&comprador='+encodeURIComponent(comp)); }
  catch(e){ el.innerHTML=`<div class="count-line">Resumos indisponíveis: ${e.message}</div>`; return; }
  const orc=o.orcamento||{}, rup=o.ruptura||{};
  const dentro=(orc.saldo||0)>=0;
  const cardOrc=resumoCard('Orçamento de compras — comprado × meta',[
    ['Meta de compras (65% venda líq. 30d)',money(orc.meta)],
    ['Comprado no mês (Winthor)',money(orc.comprado)],
    ['% da meta',orc.pct_consumido!=null?pct(orc.pct_consumido):'—'],
    ['Saldo da meta',money(orc.saldo)],
    ['Status',dentro?'DENTRO DA META':'FORA DA META'],
    ['Mês competência',orc.mes||'—'],
  ],dentro?C.green:C.red);
  const cardRup=resumoCard('Ruptura de produtos',[
    ['Itens em ruptura',int(rup.itens)],
    ['Total de produtos',int(rup.total)],
    ['% ruptura',rup.perc!=null?pct(rup.perc):'—'],
    ['Venda perdida (ruptura)',money(rup.venda_perdida)],
    ['Critério',rup.criterio||'ESTOQUE ≤ 0 E GIRO > 0'],
  ],C.red);
  el.innerHTML=`<h2 class="section"><span>Painel gerencial — resumos</span></h2>
    <div class="row">${cardOrc}${cardRup}</div>
    <div class="row">
      ${resumoTabela('Itens a vencer por faixa de validade',o.validade.faixas,o.validade.total,'itens','Itens')}
      <div class="grow" style="min-width:0">
        ${resumoTabelaGrupo('Cobertura de estoque',(o.cobertura.faixas||[]).filter(f=>{const n=parseInt(f.faixa,10);return !isNaN(n)&&n<91;}),'produtos','Produtos')}
        ${resumoTabelaGrupo('Estoque parado',(o.cobertura.faixas||[]).filter(f=>{const n=parseInt(f.faixa,10);return isNaN(n)||n>=91;}),'produtos','Produtos')}
      </div>
    </div>
    <div class="count-line">Comprado = pedido real do Winthor (pode divergir do manual da planilha). Cobertura/ruptura no escopo de produtos de revenda; números acompanham o estoque ao vivo.</div>`;
}

// paleta p/ o donut de lucro por comprador
const PAL_COMP=[C.accent,C.green,C.purple,C.orange,C.accent2,C.yellow,C.red,C.dim];
// Aba "Painel gerencial": os 5 pilares (orçamento, ruptura, validade, cobertura + participação de lucro por comprador).
function renderGerencial(P){
  const el=$('#v-gerencial');
  el.innerHTML=`<div id="gg-resumos"><div class="count-line">Carregando resumos gerenciais…</div></div>
    <h2 class="section"><span>Participação de lucro por comprador</span></h2>
    <div class="row"><div class="panel grow">
      <div class="row" style="align-items:center">
        <div style="width:230px"><div class="chart-box sm" style="height:210px"><canvas id="ch-lucrocomp"></canvas></div></div>
        <div class="grow"><table class="mini" id="gg-lucrotab"></table></div>
      </div>
      <div class="count-line" style="margin-top:6px">Lucro (venda líquida − custo) por comprador no período de venda selecionado; respeita os filtros do topo. A rosquinha mostra só participações positivas.</div>
    </div></div>`;
  injectResumos('#gg-resumos');
  // lucro por comprador — agrega os produtos filtrados
  const by={};
  P.forEach(p=>{ const nome=p.comprador||'Sem comprador'; by[nome]=(by[nome]||0)+(p.lucro||0); });
  const arr=Object.entries(by).map(([nome,lucro])=>({nome,lucro})).sort((a,b)=>b.lucro-a.lucro);
  const total=arr.reduce((s,x)=>s+x.lucro,0)||1;
  const pos=arr.filter(x=>x.lucro>0);
  const corDe=nome=>{ const i=pos.findIndex(p=>p.nome===nome); return i>=0?PAL_COMP[i%PAL_COMP.length]:C.dim; };
  chart('ch-lucrocomp',{type:'doughnut',data:{labels:pos.map(x=>x.nome),datasets:[{data:pos.map(x=>x.lucro),backgroundColor:pos.map(x=>corDe(x.nome)),borderWidth:0}]},
    options:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>pos[c.dataIndex].nome+': '+money(c.raw)+' ('+dec(c.raw/total*100,1)+'%)'}}},cutout:'62%'}});
  $('#gg-lucrotab').innerHTML=arr.map(x=>`<tr><td><span class="dot" style="background:${corDe(x.nome)}"></span> ${esc(x.nome)}</td><td class="num">${money(x.lucro)}</td><td class="num">${dec(x.lucro/total*100,1)}%</td></tr>`).join('')||'<tr><td class="muted">Sem lucro no filtro</td></tr>';
}

const OCUP_BADGE={baixa:['Baixa ocupação','#f97316'],media:['Ocupação média','#eab308'],ok:['OK','#22c55e'],sem_cubagem:['Sem cubagem','#64748b']};
const ocupBadge=v=>{const s=OCUP_BADGE[v];return s?`<span class="badge" style="background:${s[1]}22;color:${s[1]}">${s[0]}</span>`:'—';};

async function renderLogistica(useCache){
  const el=$('#v-logistica');
  let o=useCache?S.logistica:null;
  if(!o){ el.innerHTML=`<div class="loader"><div class="spinner"></div></div>`;
    try{ o=await getJSON('/api/logistica?'+serverQS()); }
    catch(e){ el.innerHTML=`<div class="empty">Logística indisponível: ${e.message}</div>`; return; }
    S.logistica=o; }
  const r=o.resumo, ps0=o.pedidos||[];
  const skL=S.sort['logistica'], ps=skL?_sortArr(ps0,skL):ps0;
  const colsL=[{k:'numped',label:'Nº',num:1},{k:'fornecedor',label:'Fornecedor'},{k:'comprador',label:'Comprador'},{k:'skus',label:'SKUs',num:1},{k:'caixas',label:'Caixas',num:1},{k:'cubagem_m3',label:'Cubagem m³',num:1},{k:'ocupacao',label:'Ocupação',num:1},{k:'valor_aberto',label:'Valor',num:1},{k:'dt_previsao',label:'Previsão'},{k:'status',label:'Status'}];
  el.innerHTML=`<h2 class="section"><span>Logística de pedidos — cubagem &amp; ocupação</span></h2>
    <div class="kpi-grid">
      ${kpi('Pedidos em aberto',int(r.n_pedidos),moneyK(r.valor_total)+' a entregar',C.accent)}
      ${kpi('Cubagem total',dec(r.cubagem_total,1)+' m³','cap. '+int(r.capacidade_m3)+' m³/veículo',C.accent2)}
      ${kpi('Baixa ocupação',int(r.n_baixa),'avaliar consolidação',C.orange)}
    </div>
    <div class="count-line">Ocupação estimada = cubagem (Σ qtd em aberto × volume unitário) ÷ capacidade do veículo (${int(r.capacidade_m3)} m³). Baixa ocupação = candidato a consolidação de carga.</div>
    <div class="panel">
      ${ps.length?`<div class="tbl-wrap"><table><thead><tr>${sortTh(colsL,skL||{})}</tr></thead>
      <tbody>${ps.map(p=>`<tr><td class="num">${p.numped}</td><td><span class="prod">${esc(p.fornecedor||'')}</span></td><td>${esc((p.comprador||'').split(' ')[0]||'—')}</td><td class="num">${int(p.skus)}</td><td class="num">${int(p.caixas)}</td><td class="num">${dec(p.cubagem_m3,2)}</td><td class="num">${p.ocupacao!=null?pct(p.ocupacao):'—'}</td><td class="num">${money(p.valor_aberto)}</td><td>${dt(p.dt_previsao)}</td><td>${ocupBadge(p.status)}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">Nenhum pedido em aberto.</div>'}
    </div>`;
  wireSortTbl($('#v-logistica'),'logistica',()=>renderLogistica(true));
}

/* ───────── planos de ação (inline) ───────── */
function planoCell(tipo,chave,cod,desc,dtval){
  const pl=S.planos[chave];
  if(pl&&(pl.acao||pl.responsavel)) return `<span class="rowact plano-set" data-tipo="${tipo}" data-chave="${esc(chave)}" data-cod="${cod}" data-desc="${esc(desc)}" data-dtval="${dtval||''}">${badge((pl.status||'').toLowerCase().replace(/ /g,'_'),pl.acao||pl.status)}</span>`;
  return `<button class="btn sm rowact plano-set" data-tipo="${tipo}" data-chave="${esc(chave)}" data-cod="${cod}" data-desc="${esc(desc)}" data-dtval="${dtval||''}">+ plano</button>`;
}
function wirePlanoCells(){ document.querySelectorAll('.plano-set').forEach(b=>b.onclick=e=>{e.stopPropagation();modalPlano({cod:+b.dataset.cod,desc:b.dataset.desc,tipo:b.dataset.tipo,chave:b.dataset.chave,dtval:b.dataset.dtval||null});}); }

/* ───────── modais ───────── */
function openModal(html,wide){ $('#modal').innerHTML=html; $('#modal').classList.toggle('wide',!!wide); $('#modal-bg').classList.add('on'); }
function closeModal(){ $('#modal-bg').classList.remove('on'); }
function modalMeta(r){ openModal(`<h3>Meta de compras — ${r.mes}</h3>
  <label>Valor da meta (R$)</label><input type="number" id="m-meta" value="${r.meta||''}" step="1000">
  <div class="m-acts"><button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Salvar</button></div>`);
  $('#m-cancel').onclick=closeModal;
  $('#m-ok').onclick=async()=>{ await postJSON('/api/orcamento/meta',{mes:r.mes,comprador:r.comprador,meta_valor:+$('#m-meta').value||0}); closeModal(); toast('Meta salva'); renderOrcamento(); };
}
// monta um item (snapshot) a partir de um produto
function _prodItem(p,qtd){ return {codprod:p.codprod,descricao:p.descricao,qtdisp:p.qtdisp,cobertura:p.cobertura,
  giro_mes:p.giro_mes,qtunitcx:p.qtunitcx,custo_unit:p.custo_unit,qtd:(qtd!=null?qtd:(p.sugestao_compra||0))}; }

// Construtor de pedido com itens. opts: produto único (do 360°) | {fornecedor,codfornec,itens} (sugestão) | null (manual)
// drill: itens comprados de um pedido REAL do Winthor (PCITEM)
async function modalPedidoItens(numped){
  openModal(`<h3>Itens comprados — pedido ${esc(numped)}</h3><div id="pi-body"><div class="loader"><div class="spinner"></div></div></div><div class="m-acts"><button class="btn" id="m-cancel">Fechar</button></div>`, true);
  $('#m-cancel').onclick=closeModal;
  try{
    const o=await getJSON('/api/pedido_itens/'+numped); const it=o.itens||[];
    $('#pi-body').innerHTML=it.length
      ? `<div class="tbl-wrap" style="max-height:360px"><table><thead><tr><th>Cód</th><th>Produto</th><th class="num">Pedida</th><th class="num">Entregue</th><th class="num">A entregar</th></tr></thead>
         <tbody>${it.map(x=>`<tr data-cod="${x.codprod}" style="cursor:pointer"><td class="num">${x.codprod}</td><td><span class="prod">${esc(x.descricao)}</span></td><td class="num">${int(x.qtped)}</td><td class="num">${int(x.qtentregue)}</td><td class="num">${x.aberto>0?int(x.aberto):'—'}</td></tr>`).join('')}</tbody></table></div>
         <div class="count-line">${it.length} itens no pedido. Clique num item p/ abrir o produto.</div>`
      : '<div class="empty">Sem itens neste pedido.</div>';
    $('#pi-body').querySelectorAll('tr[data-cod]').forEach(tr=>tr.onclick=()=>{ closeModal(); openProduto(tr.dataset.cod); });
  }catch(e){ $('#pi-body').innerHTML=`<div class="empty">Erro ao carregar itens: ${e.message}</div>`; }
}
function modalPedido(opts){
  opts=opts||{};
  let itens=[], fornIni='';
  if(opts.itens){ itens=opts.itens.map(x=>({...x})); fornIni=opts.fornecedor||''; }
  else if(opts.codprod){ itens=[_prodItem(opts)]; fornIni=opts.fornecedor||''; }
  const comp=S.compradorNome||'TODOS', hoje=new Date().toISOString().slice(0,10);
  const fdl=(S.fornecedores||[]).map(o=>`<option value="${esc(o.fornecedor)}">`).join('');
  const pdl=(S.produtosAll||[]).map(p=>`<option value="${p.codprod} — ${esc(p.descricao||'')}">`).join('');
  openModal(`<h3>${opts.itens?('Gerar pedido — '+esc(fornIni)):'Novo pedido de compra'}</h3>
    <div class="row">
      <div class="fb-group"><label>Data</label><input type="date" id="pd-data" value="${hoje}" style="width:150px"></div>
      <div class="fb-group grow" style="flex:1 1 240px"><label>Fornecedor</label><input type="text" id="pd-forn" list="pd-forn-dl" autocomplete="off" placeholder="digite e selecione…" value="${esc(fornIni)}"><datalist id="pd-forn-dl">${fdl}</datalist></div>
      <div class="fb-group"><label>Nº pedido</label><input type="text" id="pd-num" style="width:130px"></div>
      <div class="fb-group"><label>Prazo (dias)</label><input type="number" id="pd-prazo" style="width:100px"></div>
      <div class="fb-group"><label>Valor (R$)</label><input type="number" id="pd-valor" step="0.01" style="width:130px"></div>
    </div>
    <div class="d-sec">Itens do pedido</div>
    <div class="row">
      <div class="fb-group grow" style="flex:1 1 320px"><label>Adicionar produto</label><input type="text" id="pd-prodadd" list="pd-prod-dl" autocomplete="off" placeholder="código ou descrição…"><datalist id="pd-prod-dl">${pdl}</datalist></div>
      <div class="fb-group"><label>Qtd (un)</label><input type="number" id="pd-prodqt" min="1" style="width:100px"></div>
      <div class="fb-group"><label>&nbsp;</label><button class="btn" id="pd-additem">＋ Adicionar</button></div>
    </div>
    <div id="pd-itens" style="margin-top:8px"></div>
    <div class="m-acts"><button class="btn" id="m-cancel">Cancelar</button><button class="btn primary" id="m-ok">Lançar</button></div>`, true);
  const total=()=>itens.reduce((s,x)=>s+((+x.qtd||0)*(+x.custo_unit||0)),0);
  function draw(){
    $('#pd-itens').innerHTML = itens.length
      ? `<div class="tbl-wrap" style="max-height:240px"><table><thead><tr><th>Cód</th><th>Produto</th><th class="num">Qtd (un)</th><th class="num">Cx</th><th class="num">Custo</th><th class="num">Valor</th><th></th></tr></thead><tbody>`+
        itens.map((x,i)=>`<tr><td class="num">${x.codprod}</td><td><span class="prod">${esc(x.descricao||'')}</span></td>
          <td class="num"><input type="number" data-qi="${i}" value="${int(x.qtd)}" min="0" style="width:74px;text-align:right"></td>
          <td class="num">${x.qtunitcx>1?int(Math.ceil((+x.qtd||0)/x.qtunitcx))+' cx':'—'}</td>
          <td class="num"><input type="number" data-ci="${i}" value="${+x.custo_unit||0}" min="0" step="0.01" style="width:84px;text-align:right"></td><td class="num">${money((+x.qtd||0)*(+x.custo_unit||0))}</td>
          <td><button class="btn sm" data-ri="${i}">✕</button></td></tr>`).join('')+
        `</tbody></table></div><div class="count-line" style="text-align:right">Total: <b>${money(total())}</b> · ${itens.length} itens</div>`
      : `<div class="count-line">Nenhum item — adicione produtos acima${opts.itens?'':' (ou lance só com o valor)'}.</div>`;
    const v=$('#pd-valor'); if(itens.length){ v.value=total().toFixed(2); v.disabled=true; } else { v.disabled=false; }
    $('#pd-itens').querySelectorAll('[data-qi]').forEach(inp=>inp.oninput=()=>{ itens[+inp.dataset.qi].qtd=+inp.value||0; draw(); });
    $('#pd-itens').querySelectorAll('[data-ci]').forEach(inp=>inp.onchange=()=>{ itens[+inp.dataset.ci].custo_unit=+inp.value||0; draw(); });
    $('#pd-itens').querySelectorAll('[data-ri]').forEach(b=>b.onclick=()=>{ itens.splice(+b.dataset.ri,1); draw(); });
  }
  draw();
  $('#pd-additem').onclick=()=>{
    const raw=($('#pd-prodadd').value||'').trim(); const cod=parseInt(raw,10);
    const p=(S.produtosAll||[]).find(x=>x.codprod===cod)||(S.produtosAll||[]).find(x=>(x.descricao||'').toLowerCase()===raw.toLowerCase());
    if(!p){ toast('Produto não encontrado',true); return; }
    const qt=+$('#pd-prodqt').value||p.sugestao_compra||0;
    const ex=itens.find(x=>x.codprod===p.codprod); if(ex){ ex.qtd=(+ex.qtd||0)+qt; } else { itens.push(_prodItem(p,qt)); }
    if(!$('#pd-forn').value && p.fornecedor) $('#pd-forn').value=p.fornecedor;
    $('#pd-prodadd').value=''; $('#pd-prodqt').value=''; draw();
  };
  $('#m-cancel').onclick=closeModal;
  $('#m-ok').onclick=async()=>{
    const nome=($('#pd-forn').value||'').trim();
    const match=(S.fornecedores||[]).find(x=>(x.fornecedor||'').toLowerCase()===nome.toLowerCase());
    const itensPayload=itens.map(x=>({codprod:x.codprod,descricao:x.descricao,qtdisp:x.qtdisp,cobertura:x.cobertura,
      giro_mes:x.giro_mes,qtunitcx:x.qtunitcx,qtd:+x.qtd||0,custo_unit:x.custo_unit,valor:(+x.qtd||0)*(+x.custo_unit||0)}));
    const valor=itens.length?total():(+$('#pd-valor').value||0);
    await postJSON('/api/pedidos',{data_pedido:$('#pd-data').value,comprador:comp,codfornec:match?match.codfornec:(opts.codfornec||null),
      fornecedor:match?match.fornecedor:nome,n_pedido:$('#pd-num').value,valor,prazo_dias:+$('#pd-prazo').value||null,itens:itensPayload});
    closeModal(); toast('Pedido lançado ✓'); if(S.view==='orcamento')renderOrcamento(); };
}
function modalPedidoFornecedor(gr){ // "Gerar pedido" da Reposição → construtor com itens pré-preenchidos editáveis
  modalPedido({fornecedor:gr.forn, codfornec:gr.cod, itens:gr.itens.map(p=>_prodItem(p,p.sugestao_compra))});
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
    const endVal=endsByValidade(j.enderecos);   // posições por data de validade
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
      <div class="d-sec">Abastecimento (lead ${int(p.lead_efetivo)}d)</div>
      <div class="lote-row"><span>Embalagem</span><span>${embCell(p)}</span></div>
      <div class="lote-row"><span>Já pedido (aberto)</span><span>${p.qtd_ja_pedida>0?int(p.qtd_ja_pedida)+' un':'—'}</span></div>
      <div class="lote-row"><span>Estoque projetado</span><span>${int(p.estoque_projetado)} <small class="muted">(cob. ${cob(p.cobertura_proj)})</small></span></div>
      <div class="lote-row"><span>Estoque alvo</span><span>${int(p.est_alvo)}</span></div>
      <div class="lote-row"><span><b>Sugestão de compra</b></span><span><b>${sugCxN(p)}</b> ${money(p.valor_sugerido_liq)}</span></div>
      <div class="lote-row"><span>Status</span><span>${statExec(p.status_exec)}</span></div>
      ${planoDrawer(p.plano)}
      ${enderecosDrawer(j.enderecos)}
      <div class="d-sec">Lotes / validade</div>
      ${lotes.length?lotes.map(l=>{
        const d=l.dtval?String(l.dtval).slice(0,10):null, pos=(d&&endVal[d])||[];
        const sub=pos.length?`<div class="count-line" style="margin:1px 2px 9px">${pos.length} pos: ${pos.slice(0,6).join(' · ')}${pos.length>6?` <span class="muted">(+${pos.length-6})</span>`:''}</div>`:'';
        return `<div class="lote-row"><span>${dt(l.dtval)} · lote ${esc(l.numlote)}</span><span class="lr-r">${int(l.qt)} un · ${l.dias_para_vencer}d ${badge(l.classificacao)}</span></div>${sub}`;
      }).join(''):'<div class="muted" style="font-size:.8rem">Sem lotes endereçados.</div>'}
      ${(p.sugestao_compra||0)>0?`<div class="m-acts"><button class="btn primary" id="d-pedido">Registrar pedido</button></div>`:''}`;
    wireDrawer(); if($('#d-pedido'))$('#d-pedido').onclick=()=>{closeDrawer();modalPedido(p);};
    buildPlanoChart(p.plano);
  }catch(e){ dr.innerHTML='<span class="d-close">×</span><div class="empty">Erro: '+e.message+'</div>'; wireDrawer(); }
}
function wireDrawer(){ $('#drawer .d-close').onclick=closeDrawer; }
function closeDrawer(){ $('#overlay').classList.remove('on'); $('#drawer').classList.remove('on'); }

/* ───────── ocupação / WMS ───────── */
// TIPOENDER: AP = face de apanha (chão, 1 SKU/posição) · AE = pulmão (paletes, níveis altos)
const TIPO_WMS={AP:'Picking',AE:'Pulmão'};
const tipoWms=t=>TIPO_WMS[t]||t||'—';
// seção "Endereços WMS" no drawer do produto — agrupada por Picking/Pulmão
function enderecosDrawer(ends){
  if(!ends||!ends.length) return '<div class="d-sec">Endereços WMS</div><div class="muted" style="font-size:.8rem">Sem posição endereçada.</div>';
  const tot=ends.reduce((s,e)=>s+(+e.q||0),0);
  const fmt=e=>`R${int(e.rua)} · P${int(e.predio)} · N${int(e.nivel)} · A${int(e.apto)}`;
  const g={}; ends.forEach(e=>{const t=tipoWms(e.tipo);(g[t]=g[t]||[]).push(e);});
  const keys=[...new Set(['Picking','Pulmão',...Object.keys(g)])].filter(k=>g[k]);
  let html=`<div class="d-sec">Endereços WMS · ${int(ends.length)} posições · ${int(tot)} un</div>`;
  keys.forEach(t=>{
    const arr=g[t].sort((a,b)=>(b.q||0)-(a.q||0)), gt=arr.reduce((s,e)=>s+(+e.q||0),0);
    html+=`<div style="font-size:.7rem;font-weight:700;letter-spacing:.4px;color:var(--accent);text-transform:uppercase;margin:9px 2px 4px">${esc(t)} · ${arr.length} pos · ${int(gt)} un</div>`
      + arr.map(e=>`<div class="lote-row"><span class="mono">${fmt(e)}</span><span class="lr-r">${int(e.q)} un</span></div>`).join('');
  });
  return html;
}
// agrupa as posições WMS por data de validade (p/ listar embaixo de cada lote no drawer)
function endsByValidade(ends){
  const m={};
  (ends||[]).forEach(e=>{ if(e.dtval==null) return; const d=String(e.dtval).slice(0,10);
    (m[d]=m[d]||[]).push(`R${int(e.rua)}·P${int(e.predio)}·N${int(e.nivel)}·A${int(e.apto)}`); });
  return m;
}
// barras de ocupação por RUA (verde tem espaço · vermelho lotada)
function ruasHtml(ruas){
  if(!ruas.length) return '<div class="empty">Sem ruas.</div>';
  return `<div style="display:flex;flex-direction:column;gap:7px;margin-top:8px">`+ruas.map(r=>{
    const c=r.pct>=0.85?C.red:(r.pct>=0.5?C.yellow:C.green);
    return `<div style="display:flex;align-items:center;gap:10px;font-size:.8rem">
      <span class="mono" style="width:44px;color:var(--text-dim)">R${int(r.rua)}</span>
      <span class="oc-bar"><i style="width:${Math.round(r.pct*100)}%;background:${c}"></i></span>
      <span class="num" style="width:46px;text-align:right">${pct(r.pct)}</span>
      <span class="muted" style="width:78px;text-align:right;font-size:.72rem">${int(r.ocupadas)}/${int(r.posicoes)}</span>
    </div>`;
  }).join('')+`</div>`;
}
// ocupação por tipo de endereço (picking × pulmão)
function tiposHtml(tipos){
  if(!tipos.length) return '<div class="empty">—</div>';
  return `<div style="display:flex;flex-direction:column;gap:14px;margin-top:8px">`+tipos.map(t=>{
    const c=t.pct>=0.85?C.red:(t.pct>=0.5?C.yellow:C.green);
    return `<div>
      <div style="display:flex;justify-content:space-between;align-items:baseline;font-size:.88rem"><b>${esc(t.label)}</b><span class="num">${pct(t.pct)}</span></div>
      <span class="oc-bar" style="display:block;width:100%;margin-top:5px"><i style="width:${Math.round(t.pct*100)}%;background:${c}"></i></span>
      <div class="muted" style="font-size:.72rem;margin-top:3px">${int(t.ocupadas)} / ${int(t.posicoes)} posições</div>
    </div>`;
  }).join('')+`</div>`;
}
async function renderOcupacao(P){
  const el=$('#v-ocupacao'), qs=serverQS();
  let j=S._ocJ;
  if(!j || S._ocKey!==qs){   // cacheia o resumo p/ o toggle do card não re-buscar (evita flash)
    el.innerHTML=head('Ocupação do depósito (WMS)','ocupacao')+`<div class="loader"><div class="spinner"></div>Calculando ocupação…</div>`;
    try{ j=await getJSON('/api/ocupacao?'+qs); }
    catch(e){ el.innerHTML=head('Ocupação do depósito (WMS)','ocupacao')+`<div class="empty">Ocupação indisponível: ${esc(e.message)}</div>`; return; }
    S._ocJ=j; S._ocKey=qs;
  }
  const ocup=j.com_estoque||1;   // % por item = sobre as posições COM ESTOQUE (pos_end é QT>0)
  const mortos=P.filter(p=>p.espaco_morto);
  let rows=P.filter(p=>(p.pos_end||0)>0);
  if(S.ocMorto) rows=rows.filter(p=>p.espaco_morto);   // filtro na tela via card
  rows=rows.sort((a,b)=>(b.pos_end||0)-(a.pos_end||0));
  const cols=[colCod,colProd,colForn,
    {key:'pos_end',label:'Posições',num:true,fmt:int},
    {key:'pos_end',label:'% ocup.',num:true,fmt:v=>dec(v/ocup*100,1)+'%'},
    {key:'m3_end',label:'m³ end.',num:true,fmt:v=>v?dec(v,2):'—'},
    colGiroSpark,{key:'cobertura',label:'Cob.',num:true,fmt:cob},
    {key:'espaco_morto',label:'',html:p=>p.espaco_morto?`<span class="badge" style="background:${C.orange}22;color:${C.orange}">espaço morto</span>`:''}];
  // card Espaço morto = clicável, filtra a tabela "Ocupação por item" nos itens espaço morto
  const mortoCard=`<div class="card kpi" id="oc-card-morto" style="cursor:pointer${S.ocMorto?';border-color:'+C.orange:''}" title="Clique p/ filtrar a tabela nos itens espaço morto">
      <div class="k-label"><span class="dot" style="background:${C.orange}"></span>Espaço morto${S.ocMorto?` · <span style="color:${C.orange}">✕ limpar</span>`:''}</div>
      <div class="k-value">${int(mortos.length)}</div>
      <div class="k-sub">${S.ocMorto?'filtrando na tabela ↓':'ocupam muito · giram pouco · clique'}</div></div>`;
  el.innerHTML=head('Ocupação do depósito (WMS)','ocupacao')
    +`<div class="kpi-grid">
        ${kpi('Ocupação do depósito',pct(j.pct_ocupado),int(j.ocupadas)+' / '+int(j.posicoes)+' · com estoque '+pct(j.pct_com_estoque),C.accent)}
        ${kpi('Posições livres',int(j.livres),pct(j.pct_livre)+' livre',C.green)}
        ${kpi('Produtos endereçados',int(j.produtos),'no depósito',C.accent2)}
        ${kpi('Média posições/produto',dec(j.media_pos,1),'espalhamento',C.purple)}
        ${mortoCard}
      </div>
      <div class="row">
        <div class="panel grow" style="flex:2 1 420px"><h3>Ocupação por RUA <small class="muted">· ${(j.ruas||[]).length} ruas</small></h3>${ruasHtml(j.ruas||[])}
          <div class="count-line">Ocupação pelo <b>WMS (campo SITUAÇÃO)</b> — bate com o Winthor. Verde = tem espaço · amarelo = enchendo · vermelho = rua lotada.</div></div>
        <div class="grow" style="flex:1 1 240px;display:flex;flex-direction:column;gap:16px;min-width:0">
          <div class="panel" style="margin:0"><h3>Por tipo de endereço</h3>${tiposHtml(j.tipos||[])}
            <div class="count-line">Picking = face de apanha (chão) · Pulmão = paletes de armazenagem.</div></div>
          <div class="panel" id="oc-card-vazias" style="margin:0;cursor:pointer" title="Ver a lista das vagas reservadas"><h3>Reservadas vazias 🔒</h3>
            <div style="font-size:2.2rem;font-weight:800;line-height:1;color:${C.orange};font-family:'JetBrains Mono',monospace">${int(j.vazias_total||0)}</div>
            <div class="count-line" style="margin-top:7px">posições que o WMS diz ocupadas mas <b>sem mercadoria</b> · ${int(j.vazias_com_prod||0)} com produto alocado. <b>Clique p/ ver a lista ↓</b></div></div>
        </div>
      </div>
      <div class="panel"><h3>Ocupação por item <small class="muted">· ${int(rows.length)} produtos${S.ocMorto?` · <span style="color:${C.orange}">filtrando espaço morto</span>`:' endereçados'} · clique p/ ver as posições</small></h3>
        <div class="count-line">Posições = slots <b>com estoque</b> do item · <b>% ocup.</b> = sobre as ${int(j.com_estoque)} posições com estoque (não o total) · m³ = volume endereçado.</div>
        ${renderTable(rows,cols,'ocupacao')}</div>
      ${vaziasPanel(j)}`;
  const cm=$('#oc-card-morto'); if(cm) cm.onclick=()=>{ S.ocMorto=!S.ocMorto; render(); };
  const cv=$('#oc-card-vazias'); if(cv) cv.onclick=()=>{ const t=$('#oc-vazias'); if(t) t.scrollIntoView({behavior:'smooth',block:'start'}); };
}
// tabela full-width das posições ocupadas-mas-vazias (o "reservado") + produto que alocou a vaga
function vaziasPanel(j){
  const list=j.vazias||[]; if(!list.length) return '';
  const dm={}; (S.produtosAll||[]).forEach(p=>{dm[p.codprod]=p.descricao;});
  return `<div class="panel" id="oc-vazias"><h3>Posições ocupadas sem estoque — reservadas <small class="muted">· ${int(j.vazias_total)} vagas · o que reservou cada uma</small></h3>
    <div class="count-line">O WMS marca a posição como ocupada mas não há mercadoria. <b>Endereço fixo</b> → normal (a vaga é do produto, vai repor); senão, dá pra liberar. Clique p/ abrir o produto.</div>
    <div class="tbl-wrap" style="max-height:520px;overflow:auto"><table><thead><tr><th>Endereço</th><th>Tipo</th><th class="num">Cód</th><th>Produto que reservou a vaga</th></tr></thead>
    <tbody>${list.map(v=>{const nm=v.descricao||dm[v.codprod]||(v.codprod?('Produto '+v.codprod):'— sem produto');
      return `<tr ${v.codprod?`data-cod="${v.codprod}" style="cursor:pointer"`:''}><td class="mono">${esc(v.end)}</td><td>${esc(v.tipo)}</td><td class="num">${v.codprod||'—'}</td><td><span class="prod" title="${esc(nm)}">${esc(nm)}</span></td></tr>`;}).join('')}</tbody></table></div></div>`;
}

/* ───────── dispatch ───────── */
// tooltip: descrição completa ao passar o mouse — só quando o texto da coluna Produto
// está cortado (ellipsis). A descrição inteira já vem no DOM; aqui só expomos via `title`.
// Um observer no #content cobre TODAS as abas (sync e async) num ponto só, sem tocar templates.
function markProdTitles(root){
  (root || document).querySelectorAll('.prod').forEach(el=>{
    if(el.dataset.tt) return;                       // já processado nesta renderização
    el.dataset.tt = '1';
    if(el.scrollWidth > el.clientWidth + 1) el.title = el.textContent;  // só se truncado
  });
}
function startProdTitles(){
  const box = $('#content'); if(!box) return;
  new MutationObserver(muts=>{
    for(const m of muts){ if(m.addedNodes.length){ markProdTitles(); break; } }
  }).observe(box, {childList:true, subtree:true});
}
// mostra só os tabs do grupo ativo (chamado cedo no boot p/ não piscar todos os tabs no load)
function applyNav(){
  if(!$('#v-'+S.view)) S.view='cockpit';
  const g=GROUP_OF(S.view);
  document.querySelectorAll('.navgroup').forEach(x=>x.classList.toggle('active',x.dataset.group===g));
  document.querySelectorAll('.tab').forEach(t=>{ t.style.display=(t.dataset.group===g)?'':'none'; t.classList.toggle('active',t.dataset.view===S.view); });
}
function render(){
  if(!$('#v-'+S.view)) S.view='cockpit';   // view inválida/removida (ex.: 'fila' salva) → cockpit
  document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
  $('#v-'+S.view).classList.add('active');
  applyNav();
  if(S.view==='orcamento'){ renderOrcamento(); savePrefs(); return; }
  if(S.view==='logistica'){ renderLogistica(); savePrefs(); return; }
  if(S.view==='plano'){ renderPlano(); savePrefs(); return; }
  if(S.view==='desempenho'){ renderDesempenho(); savePrefs(); return; }
  const P=filtered();
  ({cockpit:renderCockpit,gerencial:renderGerencial,ruptura:renderRuptura,ruptura_comprador:renderRupturaComprador,estoque_zero:renderEstoqueZero,reposicao:renderReposicao,validade:()=>renderValidade(),parado:renderParado,comprasvendas:renderComprasVendas,abcxyz:renderABCXYZ,fornecedores:renderFornecedores,produtos:renderProdutos,qualidade:renderQualidade,ocupacao:renderOcupacao}[S.view]||renderCockpit)(P);
  savePrefs();
}
function goView(view,filt){ S.view=view; filt=filt||{}; S.cli.abast=filt.abast?(Array.isArray(filt.abast)?filt.abast:[filt.abast]):[]; S.cli.parado=filt.parado||''; S.cli.ruptura=filt.ruptura||''; S.cli.cobFaixa=filt.cobFaixa?(Array.isArray(filt.cobFaixa)?filt.cobFaixa:[filt.cobFaixa]):[]; S.cli.cobSub=''; if(filt.curva!=null){S.cli.curva=filt.curva;$('#f-curva').value=filt.curva;} render(); }

/* ───────── boot ───────── */
async function init(){
  const pr=loadPrefs();
  if(pr.vperiodo) S.vperiodo=pr.vperiodo; if(pr.params) S.params={...S.params,...pr.params};   // base fixa em gerencial (endereçado só p/ validade, que é isolada)
  if(pr.unidade) S.unidade=pr.unidade;
  if(pr.view) S.view=pr.view;
  applyNav();   // organiza os tabs já na 1ª pintura (antes do fetch) — evita o flash de todos os tabs
  document.body.classList.add('booted');   // revela os tabs (CSS esconde até aqui)
  try{
    const f=await getJSON('/api/filtros');
    S.filiaisAll=f.filiais; S.nomesFilial=f.nomes_filial||{};
    // seletor de Unidade de negócio (escopa estoque + venda)
    const unids=f.unidades||[{id:'atacado',nome:'Atacado'}];
    if(!unids.some(u=>u.id===S.unidade)) S.unidade=f.unidade_padrao||'atacado';
    $('#f-unidade').innerHTML=unids.map(u=>`<option value="${u.id}" ${u.id===S.unidade?'selected':''}>${u.cod?esc(u.cod)+' - ':''}${esc(u.nome)}</option>`).join('');
    S.fornecedores=f.fornecedores||[];
    $('#f-fornec-dl').innerHTML=f.fornecedores.map(o=>`<option value="${o.codfornec} · ${esc(o.fornecedor)}">`).join('');
    $('#f-depto').innerHTML+=f.deptos.map(d=>`<option value="${d}">${d}</option>`).join('');
    $('#f-comprador').innerHTML='<option value="">Empresa toda</option>'+f.compradores.filter(c=>c.codcomprador>0).map(c=>`<option value="${c.codcomprador}">${esc(c.comprador)}</option>`).join('');
    if(pr.comprador){ S.cli.comprador=pr.comprador; $('#f-comprador').value=pr.comprador; S.compradorNome=pr.comprador?($('#f-comprador').selectedOptions[0]?.textContent||''):''; }
  }catch(e){ toast('Falha nos filtros: '+e.message,true); }
  // base toggle visual
  // params inputs
  $('#p-lead').value=S.params.lead; $('#p-seg').value=S.params.seg; $('#p-cob').value=S.params.cob; $('#p-hor').value=S.params.hor;
  $('#p-parado').value=S.params.parado; $('#p-fcmeses').value=S.params.fcmeses;
  const giroModo=()=>S.params.sazonal?2:(S.params.forecast?1:0);  // 0=media3 1=forecast 2=sazonal
  $('#p-forecast').querySelectorAll('.seg-opt').forEach(o=>o.classList.toggle('on',+o.dataset.v===giroModo()));
  $('#p-forecast').querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{const v=+o.dataset.v;S.params.forecast=v>=1?1:0;S.params.sazonal=v===2?1:0;$('#p-forecast').querySelectorAll('.seg-opt').forEach(x=>x.classList.toggle('on',x===o));});
  $('#p-arredcx').querySelectorAll('.seg-opt').forEach(o=>o.classList.toggle('on',+o.dataset.v===(S.params.arredondacx?1:0)));
  $('#p-arredcx').querySelectorAll('.seg-opt').forEach(o=>o.onclick=()=>{S.params.arredondacx=+o.dataset.v;$('#p-arredcx').querySelectorAll('.seg-opt').forEach(x=>x.classList.toggle('on',x===o));});

  // comprador → client filter + define visão inicial
  $('#f-comprador').onchange=e=>{ S.cli.comprador=e.target.value; S.compradorNome=e.target.value?(e.target.selectedOptions[0]?.textContent||''):''; render(); };
  $('#f-unidade').onchange=e=>{S.unidade=e.target.value; S.cli.comprador=''; $('#f-comprador').value=''; S.compradorNome=''; loadData();};
  $('#f-vperiodo').value=S.vperiodo; $('#f-vperiodo').onchange=e=>{S.vperiodo=e.target.value;loadData();};
  $('#f-curva').onchange=e=>{S.cli.curva=e.target.value;render();};
  $('#f-xyz').onchange=e=>{S.cli.xyz=e.target.value;render();};
  $('#f-fornec').onchange=e=>{
    const raw=(e.target.value||'').trim(), low=raw.toLowerCase(), L=S.fornecedores||[];
    const cod=(raw.match(/^\s*(\d+)/)||[])[1];                     // código à esquerda ("708 · NOME") ou digitado puro
    let m = cod ? L.find(x=>String(x.codfornec)===cod) : null;
    if(!m) m=L.find(x=>`${x.codfornec} · ${x.fornecedor||''}`.toLowerCase()===low)  // valor exato do datalist
             ||L.find(x=>(x.fornecedor||'').toLowerCase()===low);                   // razão social exata
    if(!m){ const hits=L.filter(x=>(x.fornecedor||'').toLowerCase().includes(low)); if(hits.length===1) m=hits[0]; } // parcial só se única
    S.cli.fornec=m?String(m.codfornec):'';
    e.target.value=m?`${m.codfornec} · ${m.fornecedor}`:'';        // normaliza p/ "código · razão"; sem correspondência → volta p/ Todos
    render();
  };
  $('#f-depto').onchange=e=>{S.cli.depto=e.target.value;render();};
  document.addEventListener('click',e=>{ document.querySelectorAll('details.ms[open]').forEach(d=>{ if(!d.contains(e.target)) d.open=false; }); });
  let bt; $('#f-busca').oninput=e=>{clearTimeout(bt);bt=setTimeout(()=>{S.cli.busca=e.target.value;render();},250);};
  $('#btn-params').onclick=()=>{const p=$('#params-panel');p.style.display=p.style.display==='none'?'block':'none';};
  $('#btn-limpar').onclick=()=>{
    S.cli={comprador:'',curva:'',xyz:'',fornec:'',depto:'',busca:'',abast:[],margem:[],parado:'',ruptura:'',valDias:'',cobFaixa:[],parFaixa:[]};
    S.compradorNome='';
    ['#f-comprador','#f-curva','#f-xyz','#f-fornec','#f-depto'].forEach(s=>{const e=$(s);if(e)e.value='';});
    $('#f-busca').value='';
    render();
  };
  $('#p-apply').onclick=()=>{S.params={lead:+$('#p-lead').value,seg:+$('#p-seg').value,cob:+$('#p-cob').value,hor:+$('#p-hor').value,parado:+$('#p-parado').value||60,forecast:S.params.forecast?1:0,sazonal:S.params.sazonal?1:0,fcmeses:+$('#p-fcmeses').value||6,arredondacx:S.params.arredondacx?1:0};loadData();};
  document.querySelectorAll('.tab').forEach(t=>t.onclick=()=>{S.view=t.dataset.view;S.cli.parado='';S.cli.ruptura='';S.cli.cobFaixa=[];S.cli.cobSub='';render();});
  document.querySelectorAll('.navgroup').forEach(x=>x.onclick=()=>{ const g=x.dataset.group; if(GROUP_OF(S.view)!==g){ S.view=NAV[g][0]; S.cli.parado='';S.cli.ruptura='';S.cli.cobFaixa=[];S.cli.cobSub=''; render(); }});
  $('#overlay').onclick=closeDrawer; $('#modal-bg').onclick=e=>{if(e.target===$('#modal-bg'))closeModal();};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeDrawer();closeModal();}});
  setStickTop(); window.addEventListener('resize', setStickTop); window.addEventListener('load', setStickTop);
  startProdTitles();   // tooltip da descrição completa na coluna Produto (todas as abas)
  loadData();
}
// altura real da topbar+filterbar (ambas sticky) → offset do cabeçalho congelado das tabelas
function setStickTop(){
  const tb=$('.topbar'), fb=$('.filterbar');
  const h=(tb?tb.offsetHeight:0)+(fb?fb.offsetHeight:0);
  if(h) document.documentElement.style.setProperty('--stick-top', h+'px');
}
init();
