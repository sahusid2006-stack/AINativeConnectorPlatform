import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Health = { status: string; stage: string; product_features: string };

function App() {
  const [status, setStatus] = useState('Checking local infrastructure…');
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    fetch('/api/health', { signal: controller.signal })
      .then(async (response) => {
        const health: Health = await response.json();
        if (!response.ok || health.status !== 'ready') throw new Error('unavailable');
        setStatus('API and PostgreSQL are reachable.');
      })
      .catch(() => setStatus('Infrastructure unavailable. Check make status and make logs.'))
      .finally(() => clearTimeout(timeout));
    return () => { clearTimeout(timeout); controller.abort(); };
  }, []);
  return <main>
    <p className="eyebrow">Connector Studio · Development setup</p>
    <h1>The foundation is ready for development.</h1>
    <p role="status">{status}</p>
    <section aria-labelledby="scope">
      <h2 id="scope">Infrastructure scaffold only</h2>
      <p>Connector generation, owner authentication, provider simulators, and MCP are not implemented yet. This page does not demonstrate or verify a connector.</p>
      <p>Next: durable evidence and independent fixtures, followed by access policies and the generation path.</p>
    </section>
  </main>;
}

createRoot(document.getElementById('root')!).render(<App />);
