import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Sync } from '../../apps/web/src/Sync';
function Fixture(){const [saved,setSaved]=useState(false);return <><Sync onPublished={()=>setSaved(true)} />{saved&&<p>设置已生效</p>}</>}
createRoot(document.getElementById('root')!).render(<Fixture/>);
