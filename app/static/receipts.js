const feedback=document.querySelector('#receipt-feedback');
async function perform(url,options){
  feedback.textContent='处理中…';
  try{
    const response=await fetch(url,options);const body=await response.json();
    if(!response.ok)throw Error(body.message||'请求失败');
    location.reload();
  }catch(error){feedback.textContent=error.message;}
}
document.querySelector('#receipt-sample-run')?.addEventListener('click',()=>{
  const sample=document.querySelector('#receipt-sample').value;
  if(!sample){feedback.textContent='请选择回执样例。';return;}
  perform(`/api/runs/${window.runMeta.id}/receipts`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sample})});
});
document.querySelector('#receipt-upload')?.addEventListener('click',()=>{
  const file=document.querySelector('#receipt-file').files[0];
  if(!file){feedback.textContent='请选择银行回执CSV。';return;}
  const data=new FormData();data.append('file',file);
  perform(`/api/runs/${window.runMeta.id}/receipts`,{method:'POST',body:data});
});
document.querySelector('#seal')?.addEventListener('click',()=>perform(`/api/runs/${window.runMeta.id}/seal`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}));
for(const button of document.querySelectorAll('.resolve'))button.addEventListener('click',()=>perform(`/api/runs/${window.runMeta.id}/receipts/${button.dataset.id}/resolve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:'已核对',note:'复核员人工裁决'})}));
