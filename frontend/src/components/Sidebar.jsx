import React from 'react';
import {
  LayoutDashboard,
  ShieldAlert,
  History,
  CreditCard,
  ExternalLink,
  Bot,
  FlaskConical
} from 'lucide-react';

export default function Sidebar({ activeTab, setActiveTab }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <div className="logo-badge">
          <Bot size={22} />
        </div>
        <div className="logo-text">
          <h2>Revenue Recovery</h2>
          <span>Razorpay Track 03</span>
        </div>
      </div>

      <nav className="sidebar-nav">
        <button
          className={`nav-item ${activeTab === 'overview' ? 'active' : ''}`}
          onClick={() => setActiveTab('overview')}
        >
          <LayoutDashboard size={18} />
          <span>Dashboard Overview</span>
        </button>

        <button
          className={`nav-item ${activeTab === 'cases' ? 'active' : ''}`}
          onClick={() => setActiveTab('cases')}
        >
          <ShieldAlert size={18} />
          <span>Recovery Cases</span>
        </button>

        <button
          className={`nav-item ${activeTab === 'audit' ? 'active' : ''}`}
          onClick={() => setActiveTab('audit')}
        >
          <History size={18} />
          <span>Audit Log & Trail</span>
        </button>

        <button
          className={`nav-item ${activeTab === 'evaluation' ? 'active' : ''}`}
          onClick={() => setActiveTab('evaluation')}
        >
          <FlaskConical size={18} />
          <span>Batch Evaluation</span>
        </button>
      </nav>

      <div className="sidebar-footer">
        <div className="environment-pill">
          <div className="dot"></div>
          <span>Razorpay Test Mode</span>
        </div>
      </div>
    </aside>
  );
}
