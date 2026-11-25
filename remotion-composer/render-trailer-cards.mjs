// Installed local modules and browser only. No npx, downloads or remote assets.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const [modules,browser,request,output,bundleDir]=process.argv.slice(2);
const require=createRequire(import.meta.url);
const {bundle}=require(join(modules,'@remotion/bundler'));
const {selectComposition,renderMedia}=require(join(modules,'@remotion/renderer'));
const entry=join(dirname(fileURLToPath(import.meta.url)),'trailer-cards-entry.tsx');
const serveUrl=await bundle({entryPoint:entry,outDir:bundleDir,publicDir:null,enableCaching:false,
 webpackOverride:c=>({...c,cache:false,resolve:{...c.resolve,modules:[modules,...(c.resolve?.modules||[])]}})});
const inputProps=JSON.parse(readFileSync(request,'utf8'));
const noDownload=()=>{throw Error('Only an already installed local browser may be used.');};
const options={serveUrl,inputProps,browserExecutable:browser,onBrowserDownload:noDownload};
const composition=await selectComposition({...options,id:'TrailerCards'});
await renderMedia({...options,composition,codec:'prores',proResProfile:'4444',pixelFormat:'yuva444p10le',
 outputLocation:output,overwrite:false,concurrency:2,muted:true,imageFormat:'png',logLevel:'error'});
