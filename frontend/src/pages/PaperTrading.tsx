import React from 'react';
import SessionLauncher from '../components/SessionLauncher';

const card: React.CSSProperties = {
  background: 'var(--nd-surface)', border: '1px solid var(--nd-border)', borderRadius: 12, padding: 16,
};

const PaperTrading: React.FC = () => (
  <div>
    <div style={{ ...card, marginBottom: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span className="material-icons" style={{ color: 'var(--nd-green)', fontSize: 20, lineHeight: 1 }}>receipt_long</span>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: 'var(--nd-text-1)' }}>Paper Trading</h2>
      </div>
      <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: 'var(--nd-text-2)' }}>
        Practice on the live market with no real money. The 7-agent ensemble runs the session on the
        server — it keeps trading in the background, survives a refresh, and its trades feed Orders and
        the learning loop. Paper sessions advance only during NSE market hours (09:15–15:30 IST).
      </p>
    </div>
    <SessionLauncher mode="paper" storageKey="nd_session_paper" />
  </div>
);

export default PaperTrading;
