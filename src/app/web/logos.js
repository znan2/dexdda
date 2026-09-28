"use strict";
window.DexddaLogos = (() => {
  const hosts = new Set(["coin-images.coingecko.com","assets.coingecko.com","static.oklink.com","static.coinall.ltd","www.okx.com","web3.okx.com"]);
  let data = {chains:{},coins:{},tokens:{},networks:{}};
  const failed = new Map();
  function safe(value) {
    try {const u=new URL(value);return u.protocol==="https:" && hosts.has(u.hostname) && !u.username && !u.password && (!u.port || u.port==="443") ? u.href : null;} catch {return null;}
  }
  function paint(slot) {
    const kind=slot.dataset.logoKind,id=slot.dataset.logoId;
    const chain = kind==="network" ? data.networks?.[slot.dataset.exchange]?.[id] : id;
    const url=safe(kind==="coin" ? data.coins?.[id] || data.tokens?.[slot.dataset.token] : data.chains?.[chain]);
    if(slot.dataset.loadedUrl===url && slot.querySelector("img"))return;
    slot.replaceChildren();slot.textContent=slot.dataset.fallback || "";
    slot.hidden=!slot.textContent;
    if(!url || (failed.get(url)||0)>Date.now())return;
    const img=document.createElement("img");img.alt="";img.width=kind==="coin"?28:16;img.height=img.width;
    img.loading="lazy";img.decoding="async";img.referrerPolicy="no-referrer";
    img.addEventListener("load",()=>{slot.textContent="";slot.append(img);slot.hidden=false;});
    img.addEventListener("error",()=>{failed.set(url,Date.now()+300000);slot.textContent=slot.dataset.fallback||"";slot.hidden=!slot.textContent;delete slot.dataset.loadedUrl;});
    slot.dataset.loadedUrl=url;slot.hidden=false;slot.replaceChildren(img);img.src=url;
  }
  function icon(kind,id,fallback="",token="",exchange="") {
    const slot=document.createElement("span");slot.className=kind==="coin"?"asset-icon logo-slot":"chain-logo logo-slot";
    slot.setAttribute("aria-hidden","true");
    Object.assign(slot.dataset,{logoKind:kind,logoId:id||"",fallback,token,exchange});paint(slot);return slot;
  }
  function coin(row) {return icon("coin",row.coin_id,row.symbol?.slice(0,1)||"",row.chain_index+":"+(row.address||"").toLowerCase());}
  function chain(node,id) {node.prepend(icon("chain",String(id||"")));return node;}
  function network(node,exchange,net) {node.prepend(icon("network",net,"","",exchange));return node;}
  async function refresh() {
    try {
      const response=await fetch("/api/logos",{cache:"no-store",signal:AbortSignal.timeout(10000)});
      if(response.ok){data=await response.json();document.querySelectorAll(".logo-slot").forEach(paint);}
    } catch {} // Optional metadata never interrupts the dashboard.
    finally {setTimeout(refresh,60000);}
  }
  refresh();
  return {coin,chain,network};
})();
