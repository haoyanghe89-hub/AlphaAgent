// Confirm identical, idempotent task submissions; never create a replacement request key.
export async function postSavedTask(api,path,options,{waiting=()=>{},disposed=()=>false,wait=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}) {
  let confirmations=0,queued=0
  while(!disposed()){
    try{return await api(path,options)}catch(error){
      if(error.status===429&&queued++<24){
        waiting('已有任务正在处理，本次请求正在排队；输入和原结果保留。')
        await wait(2500)
      }else if((error.status===0||error.status>=500)&&confirmations++<1){
        waiting('正在按原任务标识确认保存结果，请稍候…')
      }else throw error
    }
  }
}
