import assert from 'node:assert/strict';
import {withTimeout} from '../app/conversion-timeout.ts';
assert.equal(await withTimeout(Promise.resolve('ok'),100,'timeout'),'ok');
await assert.rejects(withTimeout(new Promise(()=>{}),15,'下载服务未启动'),/下载服务未启动/);
await assert.rejects(withTimeout(Promise.reject(Error('offline')),100,'timeout'),/offline/);
console.log('Conversion timeout checks passed: stalled activation exits, success/errors are preserved.');
