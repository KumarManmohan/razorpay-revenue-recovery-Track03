import React, { useState, useEffect } from 'react';
import { api } from './api';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import StatsOverview from './components/StatsOverview';
import CasesTable from './components/CasesTable';
import AuditLogView from './components/AuditLogView';
import EvaluationView from './components/EvaluationView';
import CaseDetailModal from './components/CaseDetailModal';
import Toast from './components/Toast';

export default function App() {
  const [activeTab, setActiveTab] = useState('overview');
  const [stats, setStats] = useState(null);
  const [cases, setCases] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [selectedCaseId, setSelectedCaseId] = useState(null);
  const [selectedCaseData, setSelectedCaseData] = useState(null);
  const [selectedAuditTrail, setSelectedAuditTrail] = useState([]);
  const [selectedAttempts, setSelectedAttempts] = useState([]);
  const [isActionLoading, setIsActionLoading] = useState(false);
  const [toasts, setToasts] = useState([]);

  const addToast = (message, type = 'info') => {
    const id = Date.now();
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  };

  const removeToast = (id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  const fetchData = async (isManualRefresh = false) => {
    if (isManualRefresh) setIsRefreshing(true);
    else setIsLoading(true);

    try {
      const [statsRes, casesRes] = await Promise.all([
        api.getStats().catch(() => ({ stats: null })),
        api.getCases().catch(() => ({ cases: [] })),
      ]);

      setStats(statsRes?.stats || null);
      setCases(casesRes?.cases || []);

      if (isManualRefresh) {
        addToast('Dashboard data refreshed successfully.', 'info');
      }
    } catch (err) {
      console.error('Failed to fetch dashboard data:', err);
      addToast('Failed to load dashboard data from backend.', 'error');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleSelectCase = async (caseId) => {
    setSelectedCaseId(caseId);
    try {
      const detailRes = await api.getCaseDetails(caseId);
      setSelectedCaseData(detailRes.case);
      setSelectedAuditTrail(detailRes.audit || []);
      setSelectedAttempts(detailRes.attempts || []);
    } catch (err) {
      console.error('Failed to load case details:', err);
      addToast(`Could not load details for case ${caseId}`, 'error');
    }
  };

  const handleCloseModal = () => {
    setSelectedCaseId(null);
    setSelectedCaseData(null);
    setSelectedAuditTrail([]);
    setSelectedAttempts([]);
  };

  const handleApprove = async (caseId, notes) => {
    setIsActionLoading(true);
    try {
      const res = await api.approveCase(caseId, 'merchant_admin', notes);
      addToast('Case approved & Razorpay Test Payment Link generated!', 'success');
      // Refresh case details and global dashboard data
      const updatedDetail = await api.getCaseDetails(caseId);
      setSelectedCaseData(updatedDetail.case);
      setSelectedAuditTrail(updatedDetail.audit || []);
      fetchData();
    } catch (err) {
      addToast(err.message || 'Approval failed.', 'error');
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleReject = async (caseId, reason) => {
    setIsActionLoading(true);
    try {
      await api.rejectCase(caseId, 'merchant_admin', reason);
      addToast('Recovery case rejected.', 'info');
      const updatedDetail = await api.getCaseDetails(caseId);
      setSelectedCaseData(updatedDetail.case);
      setSelectedAuditTrail(updatedDetail.audit || []);
      fetchData();
    } catch (err) {
      addToast(err.message || 'Rejection failed.', 'error');
    } finally {
      setIsActionLoading(false);
    }
  };

  const handleNotify = async (caseId, recipient) => {
    setIsActionLoading(true);
    try {
      const res = await api.notifyCustomer(caseId, recipient, 'EMAIL');
      if (res.status === 'sent') {
        addToast('Test customer notification dispatched (Mock Mode).', 'success');
      } else if (res.status === 'blocked') {
        addToast('Notification blocked by Anti-Spam deduplication rule.', 'info');
      }
      // Refresh audit trail
      const updatedDetail = await api.getCaseDetails(caseId);
      setSelectedCaseData(updatedDetail.case);
      setSelectedAuditTrail(updatedDetail.audit || []);
    } catch (err) {
      addToast(err.message || 'Failed to dispatch notification.', 'error');
    } finally {
      setIsActionLoading(false);
    }
  };

  return (
    <div className="app-container">
      {/* Left Navigation Sidebar */}
      <Sidebar activeTab={activeTab} setActiveTab={setActiveTab} />

      {/* Main Content Area */}
      <div className="main-wrapper">
        <Header 
          onRefresh={() => fetchData(true)} 
          isRefreshing={isRefreshing} 
        />

        <main className="content-body">
          {activeTab === 'overview' && (
            <>
              {/* KPI Statistics */}
              <StatsOverview stats={stats} isLoading={isLoading} />

              {/* Recovery Cases Table */}
              <CasesTable 
                cases={cases} 
                isLoading={isLoading} 
                onSelectCase={handleSelectCase} 
              />
            </>
          )}

          {activeTab === 'cases' && (
            <CasesTable 
              cases={cases} 
              isLoading={isLoading} 
              onSelectCase={handleSelectCase} 
            />
          )}

          {activeTab === 'audit' && (
            <AuditLogView 
              onSelectCase={handleSelectCase} 
            />
          )}

          {activeTab === 'evaluation' && (
            <EvaluationView />
          )}
        </main>
      </div>


      {/* Case Details Drawer / Modal */}
      {selectedCaseData && (
        <CaseDetailModal
          caseData={selectedCaseData}
          auditTrail={selectedAuditTrail}
          attempts={selectedAttempts}
          onClose={handleCloseModal}
          onApprove={handleApprove}
          onReject={handleReject}
          onNotify={handleNotify}
          isActionLoading={isActionLoading}
        />
      )}

      {/* Feedback Toast Container */}
      <Toast toasts={toasts} onClose={removeToast} />
    </div>
  );
}
