import { useEffect, useState } from 'react';

// 760px = the design_reference mockup's own breakpoint. matchMedia 'change'
// events ONLY — fires exactly when the query flips, never per resize pixel,
// so there is no resize-thrash re-rendering.
const QUERY = '(max-width: 760px)';

export function useIsMobile() {
  const [mobile, setMobile] = useState(() => window.matchMedia(QUERY).matches);
  useEffect(() => {
    const mq = window.matchMedia(QUERY);
    const onChange = (e) => setMobile(e.matches);
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, []);
  return mobile;
}
