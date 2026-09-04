import {StrictMode} from 'react'; import {createRoot} from 'react-dom/client'; import Root from './App';
import '@fontsource/noto-sans-sc/chinese-simplified-400.css';
import '@fontsource/noto-sans-sc/chinese-simplified-500.css';
import '@fontsource/noto-sans-sc/chinese-simplified-600.css';
import '@fontsource/noto-sans-sc/chinese-simplified-700.css';
createRoot(document.getElementById('root')!).render(<StrictMode><Root/></StrictMode>)
