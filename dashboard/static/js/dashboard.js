/**
 * What Universal Web Dashboard JavaScript Core
 */

// Toast Notifications System
function showToast(message, type = 'success') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = 'toast-msg flex items-center gap-3 transition-all duration-300';

  let borderColor = 'border-indigo-500/40';
  let badgeColor = 'bg-indigo-500/20 text-indigo-400';
  let iconSvg = `<svg class="w-5 h-5 text-indigo-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>`;

  if (type === 'success') {
    borderColor = 'border-emerald-500/40';
    badgeColor = 'bg-emerald-500/20 text-emerald-400';
    iconSvg = `<svg class="w-5 h-5 text-emerald-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>`;
  } else if (type === 'error') {
    borderColor = 'border-rose-500/40';
    badgeColor = 'bg-rose-500/20 text-rose-400';
    iconSvg = `<svg class="w-5 h-5 text-rose-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>`;
  }

  toast.classList.add(borderColor);
  toast.innerHTML = `
    <div class="p-2 rounded-lg ${badgeColor} shrink-0">${iconSvg}</div>
    <div class="text-sm font-medium text-slate-100 flex-1 leading-snug">${message}</div>
    <button onclick="this.parentElement.remove()" class="text-slate-400 hover:text-slate-200 shrink-0 text-sm">
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
    </button>
  `;

  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(-10px)';
    setTimeout(() => toast.remove(), 300);
  }, 4500);
}

// Universal API Fetcher
async function apiCall(endpoint, method = 'GET', body = null) {
  try {
    const options = {
      method: method,
      headers: {
        'Content-Type': 'application/json'
      }
    };
    if (body) {
      options.body = JSON.stringify(body);
    }
    const response = await fetch(endpoint, options);
    if (response.status === 401) {
      window.location.href = '/login';
      return null;
    }
    const data = await response.json();
    return data;
  } catch (err) {
    console.error('API Error:', err);
    showToast('เกิดข้อผิดพลาดในการเชื่อมต่อกับเซิร์ฟเวอร์', 'error');
    return null;
  }
}

// Modal Helpers
function openModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.remove('hidden');
    modal.classList.add('flex');
  }
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }
}

// Copy to Clipboard with Feedback
function copyToClipboard(text, label = 'คัดลอกแล้ว') {
  navigator.clipboard.writeText(text).then(() => {
    showToast(`${label} เรียบร้อยแล้ว: ${text}`, 'success');
  }).catch(() => {
    showToast('ไม่สามารถคัดลอกได้', 'error');
  });
}

// Format Seconds into Duration String
function formatDuration(sec) {
  sec = Math.max(0, parseInt(sec) || 0);
  const hours = Math.floor(sec / 3600);
  const minutes = Math.floor((sec % 3600) / 60);
  const seconds = sec % 60;
  const parts = [];
  if (hours > 0) parts.push(`${hours} ชม.`);
  if (minutes > 0 || hours > 0) parts.push(`${minutes} นาที`);
  parts.push(`${seconds} วินาที`);
  return parts.join(' ');
}
