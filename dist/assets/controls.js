"use strict";
(() => {
  const $=id=>document.getElementById(id);
  const labels={upbit:"업비트",bithumb:"빗썸"};
  let catalog=[],signature="",busy=false;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  function scopes(){
    const select=$("exclusion-scope");select.replaceChildren(new Option("코인 전체 블랙리스트","coin"));
    const coin=catalog.find(c=>c.coin_id===$("exclusion-coin").value);
    for(const n of coin?.networks||[])select.add(new Option(labels[n.exchange]+" · "+(n.network_name||n.net_type)+" 임시 제외",JSON.stringify({exchange:n.exchange,net_type:n.net_type})));
  }
  function choices(){
    const selected=$("exclusion-coin").value,search=$("exclusion-search").value.trim().toLowerCase();
    $("exclusion-coin").replaceChildren();
    for(const coin of catalog.filter(c=>[c.coin_id,c.symbol,c.name].join(" ").toLowerCase().includes(search)))
      $("exclusion-coin").add(new Option(coin.symbol+" · "+coin.name+" · "+coin.coin_id,coin.coin_id));
    if([...$("exclusion-coin").options].some(o=>o.value===selected))$("exclusion-coin").value=selected;
    scopes();
  }
  function exclusions(data){
    const entries=data?.exclusions||[],next=JSON.stringify(entries);
    if(signature===next)return;signature=next;
    $("exclusions-count").textContent=entries.length;
    $("exclusion-list").replaceChildren();
    for(const e of entries){
      const row=node("div",undefined,"exclusion-entry"),coin=catalog.find(c=>c.coin_id===e.coin_id);
      row.append(node("span",(coin?coin.symbol+" · ":"")+e.coin_id+" · "+(e.kind==="coin"?"코인 전체":labels[e.exchange]+" / "+e.net_type)+ (e.reason?" · "+e.reason:"")));
      const button=node("button","해제");button.type="button";button.setAttribute("aria-label",e.coin_id+" "+(e.net_type||"전체")+" 숨김 해제");
      button.addEventListener("click",()=>change({...e,excluded:false}));row.append(button);$("exclusion-list").append(row);
    }
    if(!entries.length)$("exclusion-list").append(node("p","등록된 숨김 항목이 없습니다."));
  }
  async function post(url,body){
    const runtime=await fetch("/api/runtime",{cache:"no-store"});if(!runtime.ok)throw Error();
    const {csrf}=await runtime.json();
    const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json","X-Dexdda-Session":csrf},body:JSON.stringify(body||{}),signal:AbortSignal.timeout(45000)});
    if(!response.ok)throw Error();return response.json();
  }
  async function change(payload){
    if(busy)return;busy=true;$("exclusion-status").textContent="저장 중…";
    try{
      const data=await post("/api/preferences",payload);exclusions(data);
      $("exclusion-status").textContent=payload.excluded?"숨김 설정을 저장했습니다.":"숨김을 해제했습니다.";
      window.dispatchEvent(new CustomEvent("dexdda:preferences-changed"));
    }catch{$("exclusion-status").textContent="저장하지 못했습니다. 다시 시도하세요.";$("exclusions-panel").open=true;}
    finally{busy=false;}
  }
  $("exclusion-search").addEventListener("input",choices);$("exclusion-coin").addEventListener("change",scopes);
  $("exclusion-form").addEventListener("submit",event=>{
    event.preventDefault();const coin_id=$("exclusion-coin").value;if(!coin_id)return;
    const scope=$("exclusion-scope").value;
    change({coin_id,kind:scope==="coin"?"coin":"network",...(scope==="coin"?{}:JSON.parse(scope)),reason:$("exclusion-reason").value,excluded:true});
  });
  $("wallet-refresh").addEventListener("click",async()=>{
    $("wallet-refresh").disabled=true;
    try{await post("/api/wallet-status/refresh");window.dispatchEvent(new CustomEvent("dexdda:refresh"));}
    catch{$("wallet-status-summary").hidden=false;$("wallet-status-summary").textContent="입출금 조회 요청 실패 · 다시 시도하세요.";}
    finally{$("wallet-refresh").disabled=false;}
  });
  window.addEventListener("dexdda:snapshot",event=>{
    const d=event.detail;exclusions(d.preferences);
    const states=Object.entries(d.wallet_status||{});
    const issues=states.filter(([,s])=>s.stale || s.error).map(([ex,s])=>{
      const age=Number.isFinite(s.age_seconds)?window.DexddaUI.observedTime(Date.now()-s.age_seconds*1000):"";
      return labels[ex]+" 입출금 · "+(s.error?"조회 실패":!s.checked_at_ms?"수신 대기":"갱신 지연")+" · 상태 확인 필요"+(age?" · 마지막 수신 "+age:"");
    });
    $("wallet-status-summary").hidden=!issues.length;
    $("wallet-status-summary").textContent=issues.join(" / ");
  });
  fetch("/api/preferences",{cache:"no-store"}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(d=>{catalog=d.catalog;choices();signature="";exclusions(d);}).catch(()=>{$("exclusion-status").textContent="숨김 설정을 불러오지 못했습니다. 새로고침하세요.";});
})();
