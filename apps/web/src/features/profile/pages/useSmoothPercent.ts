import {useEffect,useRef,useState} from 'react';

export function useSmoothPercent(target:number|undefined,resetToken=0){
  const [displayed,setDisplayed]=useState(target??0);
  const displayedRef=useRef(target??0);
  const resetTokenRef=useRef(resetToken);

  useEffect(()=>{
    const shouldReset=resetTokenRef.current!==resetToken;
    if(shouldReset){
      resetTokenRef.current=resetToken;
      displayedRef.current=0;
      setDisplayed(0);
    }
    if(target===undefined){
      const resetFrame=requestAnimationFrame(()=>{
        displayedRef.current=0;
        setDisplayed(0);
      });
      return()=>cancelAnimationFrame(resetFrame);
    }

    let frame=0;
    const started=performance.now();
    const from=shouldReset?0:displayedRef.current;
    // Backend stages can report percentages from different local scales. Keep
    // one upload journey monotonic even when a later poll returns a lower value.
    const destination=Math.max(from,target);
    const duration=destination>=100?420:680;
    const animate=(now:number)=>{
      const ratio=Math.min(1,(now-started)/duration);
      const eased=1-Math.pow(1-ratio,3);
      const value=from+(destination-from)*eased;
      displayedRef.current=value;
      setDisplayed(value);
      if(ratio<1)frame=requestAnimationFrame(animate);
    };
    frame=requestAnimationFrame(animate);
    return()=>cancelAnimationFrame(frame);
  },[resetToken,target]);

  return Math.round(displayed);
}
