"use strict";
(() => {
  const $=id=>document.getElementById(id);
  const errors={wallet_missing:"지갑 주소 미설정",rpc_missing:"RPC 미설정",rpc_failed:"RPC 조회 실패",rpc_error:"RPC 조회 실패",chain_mismatch:"RPC 체인 불일치",unexpected_response:"토큰 응답 확인 필요",rate_limited:"RPC 호출 제한"};
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  let latest=null,loading=false,busy=false,session=null;
  function render(){
    if(!latest)return;
    const cards=[];
    for(const chain of latest.chains||[]){
      const card=node("article",undefined,"balance-card");card.dataset.chain=chain.chain_index;
      const title=node("h3",chain.name);window.DexddaLogos.chain(title,chain.chain_index);card.append(title);
      for(const token of chain.tokens){
        const row=node("div",undefined,"balance-row");row.dataset.symbol=token.symbol;
        const label=node("span",token.symbol,"balance-label");label.title=(token.label||token.symbol)+" · "+token.address;
        const value=node("strong",token.value===null?"—":Number(token.value)>0&&Number(token.value)<0.01?"<0.01":Number(token.value).toLocaleString("en-US",{maximumFractionDigits:2,minimumFractionDigits:2}));
        value.title=token.value===null?"잔고 미확인":token.value+" "+token.symbol;
        const age=token.observed_at_ms?window.DexddaUI.observedTime(token.observed_at_ms):null;
        const problem=token.error?(errors[token.error]||"잔고 조회 실패"):token.stale&&age?"갱신 지연":null;
        const note=node("small",problem ? problem+(age?" · 마지막 잔고 "+age:"") : age||"조회 중…",problem?"balance-error":"");
        row.append(label,value,note);card.append(row);
      }
      if(!chain.tokens.length)card.append(node("p","토큰 미등록","detail-note"));
      cards.push(card);
    }
    $("balance-cards").replaceChildren(...cards);
  }
  async function load(){
    if(loading||busy)return;loading=true;
    try{
      const r=await fetch("/api/balances",{cache:"no-store"});if(!r.ok)throw Error();
      latest=await r.json();render();$("balance-status").hidden=true;
    }catch{$("balance-status").hidden=false;$("balance-status").textContent="잔고 연결 지연 · 마지막 표시값을 유지합니다.";}
    finally{loading=false;}
  }
  $("balance-refresh").addEventListener("click",async()=>{
    if(busy||loading)return;busy=true;$("balance-refresh").disabled=true;
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),100000);
    try{
      if(!session){const r=await fetch("/api/runtime",{cache:"no-store"});if(!r.ok)throw Error();session=(await r.json()).csrf;}
      const r=await fetch("/api/balances/refresh",{method:"POST",headers:{"X-Dexdda-Session":session},cache:"no-store",signal:controller.signal});
      if(!r.ok){if(r.status===403)session=null;throw Error();}
      latest=await r.json();render();$("balance-status").hidden=true;
    }catch{$("balance-status").hidden=false;$("balance-status").textContent="잔고 갱신 실패 · 마지막 표시값을 유지합니다.";}
    finally{clearTimeout(timer);busy=false;$("balance-refresh").disabled=false;}
  });
  load();setInterval(()=>{if(!document.hidden)load();},15000);setInterval(()=>{if(!document.hidden)render();},10000);
})();
