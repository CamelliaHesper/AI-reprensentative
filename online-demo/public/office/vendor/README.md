# Vendored third-party code

| File | Source | License |
| --- | --- | --- |
| `xz-decompress.min.js` | [xz-decompress](https://www.npmjs.com/package/xz-decompress) 0.2.3 (`dist/package/xz-decompress.min.js`), a fork of [xzwasm](https://github.com/SteveSanderson/xzwasm) bundling [xz-embedded](https://tukaani.org/xz/embedded.html) compiled to WebAssembly | MIT (xz-embedded: public domain; walloc: MIT) |

Used by `../sw.js` to decompress the xz-compressed LibreOffice runtime
(about 35–40% smaller than gzip; browsers only decode gzip natively).
