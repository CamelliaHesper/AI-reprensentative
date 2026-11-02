import assert from 'node:assert/strict';
import {zip,unzip} from '../app/docx.ts';
import {collectUploads} from '../app/uploads.ts';
const enc=new TextEncoder();
const blob=zip({'材料/说明.txt':enc.encode('通知正文'),'材料/申请表.doc':enc.encode('legacy'),'材料/通知.pdf':enc.encode('pdf'),'材料/':new Uint8Array()});
const result=await collectUploads([{name:'混合材料.zip',size:blob.size,arrayBuffer:()=>blob.arrayBuffer()}],async(name,bytes)=>({name,text:new TextDecoder().decode(bytes)}),12);
assert.equal(result.items.length,2);assert.equal(result.items[0].text,'通知正文');assert.equal(result.items[1].name,'材料/申请表.doc');assert.equal(result.warnings.length,1);assert.ok(result.warnings.some(s=>s.includes('通知.pdf')));
const failedConversion=await collectUploads([{name:'混合材料.zip',size:blob.size,arrayBuffer:()=>blob.arrayBuffer()}],async(name)=>{if(name.endsWith('.doc'))throw Error('转换失败');return name;},12);
assert.deepEqual(failedConversion.items,['材料/说明.txt']);assert.ok(failedConversion.warnings.some(s=>s.includes('申请表.doc')&&s.includes('转换失败')));
const partial=await collectUploads([{name:'损坏.zip',size:1,arrayBuffer:async()=>new ArrayBuffer(1)},{name:'正常.txt',size:1,arrayBuffer:async()=>enc.encode('好').buffer}],async(name)=>name,12);
assert.deepEqual(partial.items,['正常.txt']);assert.equal(partial.warnings.length,1);
const gbk=new Uint8Array(await zip({'abcd.txt':enc.encode('test')}).arrayBuffer());const v=new DataView(gbk.buffer);for(let i=0;i<gbk.length-4;i++){if(v.getUint32(i,true)===0x04034b50){v.setUint16(i+6,0,true);gbk.set([0xb2,0xe2,0xca,0xd4],i+30);}if(v.getUint32(i,true)===0x02014b50){v.setUint16(i+8,0,true);gbk.set([0xb2,0xe2,0xca,0xd4],i+46);}}
assert.ok((await unzip(gbk))['测试.txt']);
console.log('Upload checks passed: mixed ZIP partial import, per-file failure isolation, GBK Chinese filenames.');
