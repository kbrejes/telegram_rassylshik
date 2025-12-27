/**
 * Simple client-side cache with TTL
 * Stores data in localStorage with expiration
 */
const AppCache = {
    // Cache TTL in milliseconds
    TTL: {
        status: 10000,      // 10 seconds for bot/agent status
        channels: 30000,    // 30 seconds for channels list
        vacancies: 30000,   // 30 seconds for vacancies
        agents: 15000,      // 15 seconds for agents
    },

    /**
     * Get cached data if not expired
     */
    get(key) {
        try {
            const item = localStorage.getItem(`cache_${key}`);
            if (!item) return null;

            const { data, expiry } = JSON.parse(item);
            if (Date.now() > expiry) {
                localStorage.removeItem(`cache_${key}`);
                return null;
            }
            return data;
        } catch (e) {
            return null;
        }
    },

    /**
     * Set cached data with TTL
     */
    set(key, data, ttl = 30000) {
        try {
            const item = {
                data,
                expiry: Date.now() + ttl
            };
            localStorage.setItem(`cache_${key}`, JSON.stringify(item));
        } catch (e) {
            // localStorage full or disabled
        }
    },

    /**
     * Clear specific cache key
     */
    clear(key) {
        localStorage.removeItem(`cache_${key}`);
    },

    /**
     * Clear all cache
     */
    clearAll() {
        Object.keys(localStorage)
            .filter(k => k.startsWith('cache_'))
            .forEach(k => localStorage.removeItem(k));
    }
};

/**
 * Cached fetch - returns cached data immediately, then fetches fresh
 * @param {string} url - API URL
 * @param {string} cacheKey - Cache key
 * @param {number} ttl - TTL in ms
 * @param {function} onData - Callback with data
 * @param {boolean} forceRefresh - Skip cache
 */
async function cachedFetch(url, cacheKey, ttl, onData, forceRefresh = false) {
    // Return cached data immediately if available
    if (!forceRefresh) {
        const cached = AppCache.get(cacheKey);
        if (cached) {
            onData(cached, true); // true = from cache
        }
    }

    // Fetch fresh data
    try {
        const response = await fetch(url);
        const data = await response.json();

        if (data.success !== false) {
            AppCache.set(cacheKey, data, ttl);
            onData(data, false); // false = fresh data
        }
        return data;
    } catch (error) {
        console.error(`Fetch error for ${cacheKey}:`, error);
        return null;
    }
}

/**
 * Load and cache bot status (used in sidebar)
 */
async function loadCachedStatus(onUpdate) {
    return cachedFetch(
        '/api/status',
        'status',
        AppCache.TTL.status,
        onUpdate
    );
}

/**
 * Update sidebar bot status from data
 */
function updateSidebarStatus(data) {
    if (!data || !data.success) return;

    const bot = data.status?.bot;
    const botDot = document.getElementById('sidebar-bot-dot');
    const botName = document.getElementById('sidebar-bot-name');
    const botInfo = document.getElementById('sidebar-bot-info');

    if (!botDot || !botName || !botInfo) return;

    if (bot?.connected && bot?.authorized) {
        botDot.className = 'w-2.5 h-2.5 rounded-full bg-green-500 shadow-sm shadow-green-500/50';
        if (bot.user_info) {
            botName.textContent = bot.user_info.first_name || 'Bot';
            botInfo.textContent = '@' + (bot.user_info.username || 'connected');
        } else {
            botName.textContent = 'Bot';
            botInfo.textContent = 'Connected';
        }
    } else {
        botDot.className = 'w-2.5 h-2.5 rounded-full bg-gray-400';
        botName.textContent = 'Bot';
        botInfo.textContent = 'Offline';
    }
}

/**
 * Update agent status badges in channel cards
 */
function updateAgentBadges(data) {
    if (!data || !data.success) return;

    const agents = data.status?.agents || {};
    document.querySelectorAll('[data-session]').forEach(badge => {
        const sessionName = badge.dataset.session;
        const agentStatus = agents[sessionName];
        const dot = badge.querySelector('.dot');
        const nameSpan = badge.querySelector('.name');

        if (agentStatus) {
            const status = agentStatus.status || 'disconnected';
            if (status === 'connected') {
                dot.className = 'w-2 h-2 rounded-full bg-green-500 shadow-sm shadow-green-500/50 dot';
            } else if (status === 'flood_wait') {
                dot.className = 'w-2 h-2 rounded-full bg-amber-500 shadow-sm shadow-amber-500/50 dot';
            } else if (status === 'error' || status === 'auth_expired') {
                dot.className = 'w-2 h-2 rounded-full bg-red-500 shadow-sm shadow-red-500/50 dot';
            } else {
                dot.className = 'w-2 h-2 rounded-full bg-gray-400 dot';
            }

            if (nameSpan && agentStatus.user_info) {
                const name = agentStatus.user_info.first_name || sessionName;
                const username = agentStatus.user_info.username;
                nameSpan.textContent = username ? `${name} (@${username})` : name;
            }
        } else if (dot) {
            dot.className = 'w-2 h-2 rounded-full bg-gray-400 dot';
        }
    });
}

/**
 * Combined status update - sidebar + agent badges
 */
function updateAllStatus(data) {
    updateSidebarStatus(data);
    updateAgentBadges(data);
}

/**
 * Initialize sidebar with cached status
 * Call this on every page load
 */
function initSidebar() {
    // Load immediately from cache, then refresh
    loadCachedStatus(updateSidebarStatus);

    // Refresh every 10 seconds
    setInterval(() => loadCachedStatus(updateSidebarStatus), 10000);
}

// Auto-init sidebar when DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSidebar);
} else {
    initSidebar();
}

// Sidebar toggle function (shared)
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) sidebar.classList.toggle('-translate-x-full');
    if (overlay) overlay.classList.toggle('hidden');
}

/**
 * Load and cache vacancies list
 */
async function loadCachedVacancies(onUpdate, limit = 50) {
    return cachedFetch(
        `/api/vacancies/log?limit=${limit}`,
        'vacancies',
        AppCache.TTL.vacancies,
        onUpdate
    );
}

/**
 * Load and cache vacancy messages
 */
async function loadCachedVacancyMessages(vacancyId, onUpdate) {
    return cachedFetch(
        `/api/vacancies/messages/${vacancyId}`,
        `vacancy_messages_${vacancyId}`,
        60000, // 1 minute cache for messages
        onUpdate
    );
}

/**
 * Load and cache sessions list (for agents page)
 */
async function loadCachedSessions(onUpdate) {
    return cachedFetch(
        '/api/status/sessions',
        'sessions',
        AppCache.TTL.agents,
        onUpdate
    );
}
