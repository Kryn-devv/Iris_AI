// IRIS Developer Chat Interface JavaScript Logic

document.addEventListener('DOMContentLoaded', () => {
    // DOM Elements
    const chatInput = document.getElementById('chatInput');
    const sendBtn = document.getElementById('sendBtn');
    const messagesList = document.getElementById('messagesList');
    const chatViewport = document.getElementById('chatViewport');
    const welcomeBanner = document.getElementById('welcomeBanner');
    const clearChatBtn = document.getElementById('clearChatBtn');
    const toggleToolsBtn = document.getElementById('toggleToolsBtn');
    const closeToolsBtn = document.getElementById('closeToolsBtn');
    const toolsDrawer = document.getElementById('toolsDrawer');
    const toolsList = document.getElementById('toolsList');
    const statusPill = document.getElementById('statusPill');
    const statusText = document.getElementById('statusText');
    const providerBadge = document.getElementById('providerBadge');
    const modelBadge = document.getElementById('modelBadge');
    const modeBadge = document.getElementById('modeBadge');
    const errorToast = document.getElementById('errorToast');
    const errorMsg = document.getElementById('errorMsg');

    let conversationHistory = [];
    let isSending = false;

    // Initialize System Status & Tools List
    async function initApp() {
        await fetchSystemStatus();
        await fetchRegisteredTools();
    }

    // Fetch LLM System Status
    async function fetchSystemStatus() {
        try {
            const res = await fetch('/api/v1/llm/status');
            if (!res.ok) throw new Error('Status service unavailable');
            const data = await res.json();

            statusText.textContent = data.available ? 'Online' : 'Offline';
            providerBadge.textContent = data.provider || 'mock';
            modelBadge.textContent = data.model || 'mock-model';
            modeBadge.textContent = data.mode || 'mock';

            const dot = statusPill.querySelector('.status-dot');
            if (data.available) {
                dot.classList.remove('offline');
            } else {
                dot.classList.add('offline');
            }
        } catch (err) {
            statusText.textContent = 'Offline';
            const dot = statusPill.querySelector('.status-dot');
            dot.classList.add('offline');
        }
    }

    // Fetch Registered Tools List
    async function fetchRegisteredTools() {
        try {
            const res = await fetch('/api/v1/tools');
            if (!res.ok) throw new Error('Failed to load tools');
            const tools = await res.json();

            toolsList.innerHTML = '';
            if (tools.length === 0) {
                toolsList.innerHTML = '<li>No tools registered.</li>';
                return;
            }

            tools.forEach(tool => {
                const li = document.createElement('li');
                li.innerHTML = `
                    <span><strong>${tool.name}</strong></span>
                    <span class="tool-perm">${tool.permission_level || 'READ'}</span>
                `;
                toolsList.appendChild(li);
            });
        } catch (err) {
            toolsList.innerHTML = '<li class="text-muted">Could not load tools.</li>';
        }
    }

    // Send User Message
    async function sendMessage() {
        const text = chatInput.value.trim();
        if (!text || isSending) return;

        // Hide welcome banner on first message
        if (welcomeBanner) {
            welcomeBanner.style.display = 'none';
        }

        // Render User Message
        appendMessage('user', text);
        chatInput.value = '';
        chatInput.style.height = 'auto';

        // Set Loading State
        setSendingState(true);
        const typingElem = appendTypingIndicator();

        try {
            const payload = { message: text };
            const res = await fetch('/api/v1/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            // Remove typing indicator
            typingElem.remove();

            if (!res.ok) {
                if (res.status === 400 || res.status === 422) {
                    showError('Invalid request format.');
                } else {
                    showError(`Server error (${res.status}). Please try again.`);
                }
                setSendingState(false);
                return;
            }

            const data = await res.json();
            
            // Render IRIS Response
            appendMessage('iris', data.response, {
                provider: data.provider,
                model: data.model,
                mode: data.mode,
                language: data.language,
                responseLanguage: data.response_language,
                toolsExecuted: data.tools_executed,
            });

        } catch (err) {
            typingElem.remove();
            showError('Network error. Failed to reach IRIS server.');
        } finally {
            setSendingState(false);
        }
    }

    // Helper to Append Message Row
    function appendMessage(sender, text, meta = null) {
        const row = document.createElement('div');
        row.className = `message-row ${sender}`;

        const timestamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const senderName = sender === 'user' ? 'You' : 'IRIS';

        let metaHtml = '';
        if (meta) {
            const tags = [];
            if (meta.responseLanguage) tags.push(`<span class="meta-tag">Language: ${meta.responseLanguage.toUpperCase()}</span>`);
            if (meta.provider) tags.push(`<span class="meta-tag">Provider: ${meta.provider}</span>`);
            if (meta.model) tags.push(`<span class="meta-tag">Model: ${meta.model}</span>`);
            if (meta.mode) tags.push(`<span class="meta-tag">Mode: ${meta.mode}</span>`);
            
            if (meta.toolsExecuted && meta.toolsExecuted.length > 0) {
                meta.toolsExecuted.forEach(t => {
                    tags.push(`<span class="tool-tag">Tool: ${t.tool_name}</span>`);
                });
            }
            if (tags.length > 0) {
                metaHtml = `<div class="meta-footer">${tags.join('')}</div>`;
            }
        }

        row.innerHTML = `
            <div class="message-header">
                <span>${senderName}</span>
                <span>•</span>
                <span>${timestamp}</span>
            </div>
            <div class="message-bubble">${escapeHtml(text)}</div>
            ${metaHtml}
        `;

        messagesList.appendChild(row);
        scrollToBottom();
    }

    // Helper for Typing Indicator
    function appendTypingIndicator() {
        const row = document.createElement('div');
        row.className = 'message-row iris';
        row.innerHTML = `
            <div class="message-header">
                <span>IRIS is thinking...</span>
            </div>
            <div class="typing-indicator">
                <div class="dot"></div>
                <div class="dot"></div>
                <div class="dot"></div>
            </div>
        `;
        messagesList.appendChild(row);
        scrollToBottom();
        return row;
    }

    function setSendingState(sending) {
        isSending = sending;
        sendBtn.disabled = sending;
    }

    function scrollToBottom() {
        chatViewport.scrollTop = chatViewport.scrollHeight;
    }

    function showError(message) {
        errorMsg.textContent = message;
        errorToast.classList.remove('hidden');
        setTimeout(() => {
            hideError();
        }, 5000);
    }

    window.hideError = function() {
        errorToast.classList.add('hidden');
    };

    window.sendPrompt = function(promptText) {
        chatInput.value = promptText;
        sendMessage();
    };

    function escapeHtml(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    // Event Listeners
    sendBtn.addEventListener('click', sendMessage);

    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    clearChatBtn.addEventListener('click', () => {
        messagesList.innerHTML = '';
        if (welcomeBanner) {
            welcomeBanner.style.display = 'block';
        }
    });

    toggleToolsBtn.addEventListener('click', () => {
        toolsDrawer.classList.toggle('hidden');
    });

    closeToolsBtn.addEventListener('click', () => {
        toolsDrawer.classList.add('hidden');
    });

    // Auto-expand input textarea
    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = chatInput.scrollHeight + 'px';
    });

    // Initialize
    initApp();
});
