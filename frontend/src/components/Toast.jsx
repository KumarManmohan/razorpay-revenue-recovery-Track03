import React from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

export default function Toast({ toasts, onClose }) {
  if (!toasts || toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.type || 'info'}`}>
          {toast.type === 'success' && <CheckCircle2 size={16} color="var(--success)" />}
          {toast.type === 'error' && <AlertCircle size={16} color="var(--danger)" />}
          {toast.type === 'info' && <Info size={16} color="var(--primary)" />}
          
          <span style={{ flex: 1 }}>{toast.message}</span>

          <button
            style={{ background: 'transparent', border: 'none', color: 'var(--text-muted)', cursor: 'pointer', padding: '2px' }}
            onClick={() => onClose(toast.id)}
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
