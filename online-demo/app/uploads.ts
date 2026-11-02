import {unzip} from './docx.ts';
export type UploadSource={name:string,size:number,arrayBuffer:()=>Promise<ArrayBuffer>};
export async function collectUploads<T>(sources:UploadSource[],read:(name:string,bytes:Uint8Array)=>Promise<T>,limit:number){
 const items:T[]=[],warnings:string[]=[];
 const add=async(name:string,bytes:Uint8Array)=>{
  if(!/\.(docx?|txt|csv|md)$/i.test(name)){warnings.push(`${name}：${/\.pdf$/i.test(name)?'在线版暂不读取 PDF，请将相关通知文字粘贴到正文':/\.xlsx?$/i.test(name)?'在线版暂不读取 Excel，可另存为 CSV 后上传':'暂不支持此类型'}`);return;}
  if(items.length>=limit){warnings.push(`${name}：超过12份材料上限，本次未导入`);return;}
  try{items.push(await read(name,bytes));}catch(e){warnings.push(`${name}：${e instanceof Error?e.message:'读取失败'}`);}
 };
 for(const source of sources){try{
  if(source.size>10000000)throw Error('单文件最多10MB');
  const bytes=new Uint8Array(await source.arrayBuffer());
  if(/\.zip$/i.test(source.name)){
   const entries=await unzip(bytes);let count=0;
   for(const [name,data]of Object.entries(entries)){if(name.endsWith('/')||name.startsWith('__MACOSX/')||name.split('/').pop()?.startsWith('.'))continue;count++;await add(name,data);}
   if(!count)warnings.push(`${source.name}：压缩包内没有可导入的文件`);
  }else await add(source.name,bytes);
 }catch(e){warnings.push(`${source.name}：${e instanceof Error?e.message:'读取失败'}`);}}
 return {items,warnings};
}
