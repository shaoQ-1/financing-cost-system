/* ═══════════════════════════════════════════
   Treasury System — Shared JS Utilities
   ═══════════════════════════════════════════ */

(function() {
  'use strict';

  // ── Loading Overlay ──
  window.showLoading = function(title, subtitle) {
    let overlay = document.getElementById('loadingOverlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'loadingOverlay';
      overlay.className = 'loading-overlay';
      overlay.innerHTML =
        '<div class="loading-content">' +
          '<div class="loading-spinner"></div>' +
          '<div class="loading-title" id="loadingTitle">处理中...</div>' +
          '<div class="loading-subtitle" id="loadingSubtitle">请稍候</div>' +
        '</div>';
      document.body.appendChild(overlay);
    }
    document.getElementById('loadingTitle').textContent = title || '处理中...';
    document.getElementById('loadingSubtitle').textContent = subtitle || '请稍候';
    overlay.classList.add('show');
  };

  window.hideLoading = function() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.classList.remove('show');
  };

  // ── Polling ──
  window.startPolling = function(url, onDone, onError) {
    hideLoading();
    const interval = setInterval(function() {
      fetch(url)
        .then(r => r.json())
        .then(data => {
          if (data.status === 'done') {
            clearInterval(interval);
            hideLoading();
            if (onDone) onDone(data);
          } else if (data.status === 'error') {
            clearInterval(interval);
            hideLoading();
            if (onError) onError(data);
            else alert('处理失败: ' + (data.message || '未知错误'));
          }
        })
        .catch(function(e) {
          // network error, keep trying
        });
    }, 2000);
    return interval;
  };

  // ── Toast Notification ──
  window.showToast = function(message, type) {
    type = type || 'info';
    var toast = document.createElement('div');
    toast.style.cssText =
      'position:fixed;bottom:24px;right:24px;padding:12px 20px;border-radius:10px;' +
      'font-size:0.9rem;font-weight:500;z-index:10000;' +
      'background:#1e293b;border:1px solid rgba(148,163,184,0.12);' +
      'color:#f1f5f9;box-shadow:0 8px 32px rgba(0,0,0,0.4);' +
      'animation:fadeInUp 0.3s cubic-bezier(0.16,1,0.3,1) both;' +
      'max-width:400px;';
    if (type === 'error') toast.style.borderColor = 'rgba(239,68,68,0.3)';
    if (type === 'success') toast.style.borderColor = 'rgba(16,185,129,0.3)';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function() { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; setTimeout(function() { toast.remove(); }, 300); }, 3500);
  };

  // ── htmx redirect helper ──
  document.addEventListener('htmx:afterRequest', function(evt) {
    try {
      var data = JSON.parse(evt.detail.xhr.responseText);
      if (data.redirect) {
        window.location.href = data.redirect;
      }
      if (data.error) {
        showToast(data.error, 'error');
      }
    } catch(e) {}
  });

  // ── Animate elements on scroll ──
  document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.animate-on-view').forEach(function(el, i) {
      el.style.opacity = '0';
      el.style.transform = 'translateY(16px)';
      el.style.transition = 'all 0.6s cubic-bezier(0.16,1,0.3,1)';
      var observer = new IntersectionObserver(function(entries) {
        entries.forEach(function(entry) {
          if (entry.isIntersecting) {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
            observer.unobserve(el);
          }
        });
      }, { threshold: 0.1 });
      observer.observe(el);
    });
  });

})();
