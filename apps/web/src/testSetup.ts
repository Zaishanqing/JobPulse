import '@testing-library/jest-dom/vitest';
import {configure} from '@testing-library/dom';

configure({asyncUtilTimeout:10000});
Object.defineProperty(window,'matchMedia',{writable:true,value:(query:string)=>({matches:false,media:query,onchange:null,addListener:()=>undefined,removeListener:()=>undefined,addEventListener:()=>undefined,removeEventListener:()=>undefined,dispatchEvent:()=>false})});
class ResizeObserverMock {observe(){} unobserve(){} disconnect(){}}
Object.defineProperty(window,'ResizeObserver',{writable:true,value:ResizeObserverMock});
Object.defineProperty(globalThis,'ResizeObserver',{writable:true,value:ResizeObserverMock});
