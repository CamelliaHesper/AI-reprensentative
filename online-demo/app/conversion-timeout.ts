export function withTimeout<T>(work:Promise<T>,ms:number,message:string):Promise<T>{
 return new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(message)),ms);work.then(value=>{clearTimeout(timer);resolve(value);},error=>{clearTimeout(timer);reject(error);});});
}
