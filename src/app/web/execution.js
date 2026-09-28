"use strict";
(() => {
  const $=id=>document.getElementById(id), dialog=$("swap-dialog");
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  let runtime=null,latest=null,intent=null,busy=false,pollTimer=null,version=0;
  const info=new Set(["deposit_reference","gas_estimate","time_estimate","confirmations_unavailable"]);
  const labels={awaiting_confirmation:"아래 내용을 확인한 후 진행하세요.",rechecking:"입금 상태·견적·잔고를 다시 확인하고 있습니다.",
    pending:"전송 결과 확인 중입니다. 중복 전송하지 않고 영수증을 조회합니다.",submission_unknown:"전송 결과가 불확실합니다. 새 전송 없이 기존 해시를 확인합니다.",
    dry_run_complete:"DRY_RUN 완료 · 승인·스왑 서명 및 전송 0회",success:"스왑 성공 · 거래소 전송은 직접 진행하세요.",
    reverted:"트랜잭션이 revert되었습니다. 스왑 완료가 아닙니다.",rejected:"실행 가드가 중단했습니다.",failed:"실행을 완료하지 못했습니다.",
    approval_complete_reconfirm:"승인까지 확인했습니다. 현재 가격으로 새 견적·확인이 필요합니다."};
  const errorText={amount_limit:"1회 금액 한도 초과",token_balance_insufficient:"선택한 매수 자산 잔고 부족",gas_balance_insufficient:"네이티브 가스 잔고 부족",
    nonce_conflict:"지갑 nonce 충돌 · 자동 재전송 없음",receipt_timeout:"영수증 대기 시간 초과 · 조회로 상태 확인",
    quote_deteriorated:"확인한 최소 수령량보다 견적 악화",gap_below_minimum:"최소 실효갭 미달",history_pending:"실제 수령량 집계 대기",
    interrupted:"서버 중단 후 상태 재확인 필요",new_confirmation_required:"새 확인 필요",transaction_reverted:"트랜잭션 revert"};
  async function api(path,body){
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),100000);
    try{
      const response=await fetch(path,{method:body?"POST":"GET",headers:body?{"Content-Type":"application/json","X-Dexdda-Session":runtime?.csrf||""}:{},
        body:body?JSON.stringify(body):undefined,cache:"no-store",signal:controller.signal});
      const data=await response.json();
      if(!response.ok)throw Error(data.message||errorText[data.error]||"요청 실패 · "+(data.error||response.status));
      return data;
    }finally{clearTimeout(timeout);}
  }
  function action(){
    const button=$("detail-swap");
    if(!button)return;
    const q=latest?.quote;
    const eligible=runtime?.swap_available&&q&&q.result&&!latest.expired&&q.route_status==="tradable"&&
      !(q.warnings||[]).some(w=>!info.has(w.code))&&Number(q.result.gap_percent)>=Number(runtime.settings.min_gap_percent);
    button.disabled=!eligible||busy;
    button.textContent=busy?"실행 데이터 준비 중…":runtime?.dry_run?"DRY_RUN 실행 준비":"스왑 실행 준비";
    if(!eligible)button.title="유효한 실효갭·직접 입금 경로와 실행 가드 확인이 필요합니다.";
    else button.title="먼저 확인 모달이 열립니다.";
  }
  window.addEventListener("dexdda:detail",e=>{latest=e.detail;action();});
  window.addEventListener("dexdda:detail-invalidated",()=>{latest=null;action();});
  const details=(values)=>{
    const dl=node("dl",undefined,"detail-values");
    for(const [key,value] of values)dl.append(node("dt",key),node("dd",String(value??"—")));
    return dl;
  };
  function render(entry){
    intent=entry;$("swap-title").textContent=entry.dry_run?"DRY_RUN 확인":"스왑 실행 확인";
    $("swap-status").textContent=(labels[entry.status]||entry.status)+(entry.error?" · "+(errorText[entry.error]||entry.error):"");
    const s=entry.summary,c=$("swap-content");c.replaceChildren();
    c.append(details([["코인",s.token_name+" · "+s.symbol],["체인",(s.chain_name||s.chain_index)+" ("+s.chain_index+")"],
      ["컨트랙트",s.address],["주소 대조",s.address.slice(0,8)+" … "+s.address.slice(-6)],["매도 거래소",s.exchange==="upbit"?"업비트":"빗썸"],
      ["매수 자산 CA",s.purchase_address||"—"],["투입 금액",s.amount_usdt+" "+(s.purchase_symbol||"USDT")],["슬리피지",s.slippage_percent+"%"],["예상 수령량",s.expected_quantity],
      ["최소 수령량",s.minimum_quantity],["승인 횟수·상한",s.approval_count+"회 · "+s.approval_amount+" "+(s.purchase_symbol||"USDT")+" ("+s.approval_policy+")"],
      ["네이티브 가스 예산",s.gas_budget_native],["직전 실효갭",Number(s.gap_percent).toFixed(3)+"%"]]));
    if(entry.status==="awaiting_confirmation"){
      c.append(node("p",entry.dry_run?"이 확인은 DRY_RUN입니다. 개인키 서명과 네트워크 전송을 수행하지 않습니다.":
        "확인하면 필요한 토큰 승인과 스왑 트랜잭션을 전송합니다. 승인 상한과 최소 수령량을 확인하세요.","notice"));
      $("swap-confirm").hidden=false;$("swap-confirm").disabled=false;
      $("swap-confirm").textContent=entry.dry_run?"확인 · DRY_RUN 진행":"확인 · 승인 및 스왑 전송";
    }else $("swap-confirm").hidden=true;
    for(const tx of entry.transactions||[]){
      c.append(node("p",tx.kind+" · "+tx.status,"detail-note"),node("code",tx.hash,"tx-hash"));
    }
    if(entry.planned_transactions?.length){
      c.append(node("h3","전송하지 않은 트랜잭션 계획"));
      for(const tx of entry.planned_transactions)c.append(node("p",tx.kind+" · nonce "+tx.nonce+" · gas "+tx.gas,"detail-note"));
    }
    if(entry.status==="success")c.append(node("p","실제 수령량: "+(entry.actual_quantity??"집계 대기"),"actual-received"));
    $("swap-recheck").hidden=entry.status==="awaiting_confirmation"||entry.status==="dry_run_complete";
  }
  async function completion(id,v){
    try{
      const result=await api("/api/swap/"+id+"/completion");
      if(v!==version||!dialog.open)return;
      const c=$("swap-content"),section=node("section",undefined,"deposit-completion");
      section.append(node("h3","거래소 입금 주소"));
      if(!result.addresses.length)section.append(node("p","현재 체인과 일치하는 준비된 주소가 없습니다."));
      for(const item of result.addresses){
        const card=node("div",undefined,"deposit-card");
        card.append(node("h4",(item.exchange==="upbit"?"업비트":"빗썸")+" · "+item.network_name+" ["+item.net_type+"]"));
        card.append(node("p",item.deposit_possible?"입금 가능 · 이번 조회 기준":"입금 중단 또는 상태 미확인","detail-note"));
        if(item.reference_only)card.append(node("p","업비트 상태 정보는 지연될 수 있는 참고 정보입니다.","detail-note"));
        if(!item.deposit_address){card.append(node("p","주소 준비 필요 · "+(item.error||item.status)));section.append(card);continue;}
        card.append(node("code",item.deposit_address,"deposit-address"));
        const verify=node("p","복사 후 앞뒤 6자리를 대조하세요.","copy-verification");
        function copyButton(label,value,isAddress){
          const button=node("button",label);button.type="button";button.disabled=!item.deposit_possible;
          button.addEventListener("click",async()=>{
            try{await navigator.clipboard.writeText(value);verify.textContent=isAddress?"복사한 주소: "+value.slice(0,8)+" … "+value.slice(-6):"메모·태그 복사 완료: "+value;}
            catch{verify.textContent="자동 복사에 실패했습니다. 표시된 값을 직접 복사하세요.";}
          });return button;
        }
        card.append(copyButton("입금 주소 복사",item.deposit_address,true));
        if(item.secondary_address){card.append(node("p","메모·태그: "+item.secondary_address),copyButton("메모·태그 복사",item.secondary_address,false));}
        card.append(verify);section.append(card);
      }
      c.append(section);
    }catch(error){if(v===version)$("swap-content").append(node("p",error.message,"notice error"));}
  }
  async function status(id,v){
    try{
      const entry=await api("/api/swap/"+id);
      if(v!==version||!dialog.open)return;
      render(entry);
      if(entry.status==="success")await completion(id,v);
      if(["rechecking","pending","submission_unknown"].includes(entry.status)){
        pollTimer=setTimeout(()=>status(id,v),2500);
      }
      refreshHistory();
    }catch(error){
      if(v===version){$("swap-status").textContent=error.message+" · 재전송하지 않았습니다.";$("swap-recheck").hidden=false;}
    }
  }
  $("detail-swap").addEventListener("click",async()=>{
    if(busy||!latest)return;
    const input={...latest.request},v=++version;busy=true;action();
    intent=null;$("swap-content").replaceChildren();$("swap-confirm").hidden=true;$("swap-recheck").hidden=true;
    $("swap-status").textContent="최신 견적·잔고·승인·가스·nonce 검사 중…";
    if(!dialog.open)dialog.showModal();
    try{
      const entry=await api("/api/swap/prepare",input);
      if(v===version&&dialog.open)render(entry);
    }catch(error){if(v===version)$("swap-status").textContent=error.message;}
    finally{busy=false;action();}
  });
  $("swap-confirm").addEventListener("click",async()=>{
    if(!intent||intent.status!=="awaiting_confirmation")return;
    $("swap-confirm").disabled=true;const id=intent.id,v=version;
    try{
      const entry=await api("/api/swap/confirm",{intent_id:id,confirmed:true});
      if(v===version&&dialog.open){render(entry);await status(id,v);}
    }catch(error){
      if(v===version){$("swap-status").textContent=error.message+" · 기존 요청 상태를 조회하세요.";$("swap-confirm").hidden=true;$("swap-recheck").hidden=false;}
    }
  });
  $("swap-recheck").addEventListener("click",()=>{if(intent){clearTimeout(pollTimer);status(intent.id,version);}});
  $("swap-close").addEventListener("click",()=>dialog.close());
  dialog.addEventListener("close",()=>{if(!dialog.open){version++;clearTimeout(pollTimer);}});
  async function refreshHistory(){
    try{
      const data=await api("/api/swaps");const target=$("swap-history");target.replaceChildren();
      if(!data.items.length){target.append(node("p","아직 실행 기록이 없습니다.","detail-note"));return;}
      for(const entry of data.items){
        const button=node("button",new Date(entry.created_at_ms).toLocaleString("ko-KR")+" · "+entry.summary.symbol+" · "+(entry.dry_run?"DRY_RUN · ":"")+(labels[entry.status]||entry.status),"history-entry");
        button.type="button";button.addEventListener("click",()=>{
          version++;clearTimeout(pollTimer);render(entry);if(!dialog.open)dialog.showModal();
          status(entry.id,version);
        });target.append(button);
      }
    }catch{$("swap-history").textContent="이력 조회 실패 · 자동으로 다시 확인합니다.";}
  }
  async function operations(){
    try{
      const data=await api("/api/operations");const lines=[],pending=[];
      for(const [ex,item] of Object.entries(data.markets||{})){
        if(item.new.length)lines.push((ex==="upbit"?"업비트":"빗썸")+": 새 종목 감지 · "+item.new.join(", ")+" · 등록 확인 필요");
        for(const entry of item.mapping_pending || [])pending.push((ex==="upbit"?"업비트":"빗썸")+" · "+entry.market+" · "+entry.reason);
        if(item.error)lines.push((ex==="upbit"?"업비트":"빗썸")+" 신규 상장 조회 실패");
      }
      $("registry-status").hidden=!pending.length;
      $("registry-pending-count").textContent=pending.length;
      $("registry-pending-list").replaceChildren(...pending.map(text=>node("p",text)));
      const notice=$("operations-notice");notice.hidden=!lines.length;notice.textContent=lines.join(" / ");
    }catch{
      $("operations-notice").hidden=false;
      $("operations-notice").textContent="운영 정보 조회 실패 · 가격·갭은 별도 갱신";
    }
  }
  api("/api/runtime").then(data=>{
    runtime=data;action();
    document.querySelector(".readonly").textContent=data.dry_run?"DRY_RUN · 전송 없음":"실거래 · 확인 후 전송";
  }).catch(()=>{runtime=null;action();});
  operations();refreshHistory();
  setInterval(()=>{operations();refreshHistory();},15000);
})();
