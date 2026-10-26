/* Drill console - shell interactions
   Theme toggle, responsive sidebar, flash/toast, copy, confirm dialog, lightbox.
   No dependencies. */
(function () {
    'use strict';

    var root = document.documentElement;
    var THEME_KEY = 'fish-theme';

    function storageGet(key) {
        try {
            return window.localStorage.getItem(key);
        } catch (e) {
            return null;
        }
    }

    function storageSet(key, value) {
        try {
            window.localStorage.setItem(key, value);
        } catch (e) {
            /* ignore */
        }
    }

    function currentTheme() {
        return root.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    }

    function syncThemeButtons(theme) {
        var label = theme === 'dark' ? '切换到浅色主题' : '切换到深色主题';
        document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
            btn.setAttribute('aria-label', label);
            btn.setAttribute('title', label);
        });
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) {
            meta.setAttribute('content', theme === 'dark' ? '#010102' : '#f7f8f8');
        }
    }

    function setTheme(theme, persist) {
        root.setAttribute('data-theme', theme);
        if (persist) {
            storageSet(THEME_KEY, theme);
        }
        syncThemeButtons(theme);
    }

    syncThemeButtons(currentTheme());

    document.addEventListener('click', function (event) {
        var toggle = event.target.closest('[data-theme-toggle]');
        if (!toggle) {
            return;
        }
        event.preventDefault();
        setTheme(currentTheme() === 'dark' ? 'light' : 'dark', true);
    });

    /* ---- Responsive sidebar ---- */
    var sidebar = document.getElementById('sidebar');
    var backdrop = document.getElementById('sidebarBackdrop');
    var menuBtn = document.getElementById('menuBtn');

    function openSidebar() {
        if (!sidebar) {
            return;
        }
        sidebar.classList.add('open');
        if (backdrop) {
            backdrop.classList.add('open');
        }
        if (menuBtn) {
            menuBtn.setAttribute('aria-expanded', 'true');
        }
    }

    function closeSidebar() {
        if (!sidebar) {
            return;
        }
        sidebar.classList.remove('open');
        if (backdrop) {
            backdrop.classList.remove('open');
        }
        if (menuBtn) {
            menuBtn.setAttribute('aria-expanded', 'false');
        }
    }

    if (menuBtn) {
        menuBtn.addEventListener('click', function () {
            if (sidebar && sidebar.classList.contains('open')) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });
    }

    if (backdrop) {
        backdrop.addEventListener('click', closeSidebar);
    }

    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && sidebar && sidebar.classList.contains('open')) {
            closeSidebar();
        }
    });

    if (sidebar) {
        sidebar.addEventListener('click', function (event) {
            if (event.target.closest('.nav-item, .logout-link') && window.innerWidth <= 820) {
                closeSidebar();
            }
        });
    }

    /* ---- Flash messages ---- */
    document.querySelectorAll('.flash').forEach(function (flash) {
        var closed = false;

        function dismiss() {
            if (closed) {
                return;
            }
            closed = true;
            flash.classList.add('is-leaving');
            window.setTimeout(function () {
                if (flash.parentNode) {
                    flash.parentNode.removeChild(flash);
                }
            }, 200);
        }

        var close = flash.querySelector('.flash-close');
        if (close) {
            close.addEventListener('click', dismiss);
        }
        window.setTimeout(dismiss, 6000);
    });

    /* ---- Toast ---- */
    var toastStack = null;

    function ensureToastStack() {
        if (!toastStack) {
            toastStack = document.createElement('div');
            toastStack.className = 'toast-stack';
            document.body.appendChild(toastStack);
        }
        return toastStack;
    }

    function toast(message, type) {
        var stack = ensureToastStack();
        var node = document.createElement('div');
        node.className = 'toast' + (type ? ' toast-' + type : '');
        node.setAttribute('role', 'status');

        var icon = document.createElement('svg');
        icon.setAttribute('class', 'icon');
        icon.setAttribute('aria-hidden', 'true');
        var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
        use.setAttribute('href', type === 'error' ? '#i-alert-circle' : '#i-check-circle');
        icon.appendChild(use);

        var text = document.createElement('div');
        text.textContent = message;

        node.appendChild(icon);
        node.appendChild(text);
        stack.appendChild(node);

        window.setTimeout(function () {
            node.style.opacity = '0';
            node.style.transform = 'translateY(4px)';
            window.setTimeout(function () {
                if (node.parentNode) {
                    node.parentNode.removeChild(node);
                }
            }, 180);
        }, 3200);
    }

    /* ---- Copy to clipboard ---- */
    function copyText(text) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            var area = document.createElement('textarea');
            area.value = text;
            area.setAttribute('readonly', 'readonly');
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            try {
                var ok = document.execCommand('copy');
                area.parentNode.removeChild(area);
                if (ok) {
                    resolve();
                } else {
                    reject(new Error('copy rejected'));
                }
            } catch (err) {
                area.parentNode.removeChild(area);
                reject(err);
            }
        });
    }

    document.addEventListener('click', function (event) {
        var chip = event.target.closest('[data-copy]');
        if (!chip) {
            return;
        }
        event.preventDefault();
        var value = chip.getAttribute('data-copy') || '';
        copyText(value)
            .then(function () {
                toast('已复制到剪贴板', 'success');
                chip.classList.add('is-copied');
                window.setTimeout(function () {
                    chip.classList.remove('is-copied');
                }, 1600);
            })
            .catch(function () {
                toast('复制失败，请手动选择文本', 'error');
            });
    });

    /* ---- Modal helpers ---- */
    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (ch) {
            return {
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[ch];
        });
    }

    function createBackdrop() {
        var node = document.createElement('div');
        node.className = 'modal-backdrop';
        document.body.appendChild(node);
        window.requestAnimationFrame(function () {
            node.classList.add('open');
        });
        return node;
    }

    function removeBackdrop(node) {
        node.classList.remove('open');
        window.setTimeout(function () {
            if (node.parentNode) {
                node.parentNode.removeChild(node);
            }
        }, 180);
    }

    function confirmDialog(message, options) {
        options = options || {};
        return new Promise(function (resolve) {
            var backdrop = createBackdrop();
            var danger = !!options.danger;
            var title = options.title || (danger ? '确认删除' : '确认操作');
            var okText = options.okText || '确认';
            var iconId = danger ? '#i-alert' : '#i-info';

            backdrop.innerHTML =
                '<div class="modal" role="dialog" aria-modal="true" aria-labelledby="fishModalTitle">' +
                '<div class="modal-head">' +
                '<span class="modal-icon' + (danger ? ' is-danger' : '') + '">' +
                '<svg class="icon" aria-hidden="true"><use href="' + iconId + '"></use></svg>' +
                '</span>' +
                '<div>' +
                '<div class="modal-title" id="fishModalTitle">' + escapeHtml(title) + '</div>' +
                '<div class="modal-text">' + escapeHtml(message) + '</div>' +
                '</div>' +
                '</div>' +
                '<div class="modal-foot">' +
                '<button type="button" class="btn btn-secondary" data-act="cancel">取消</button>' +
                '<button type="button" class="btn ' + (danger ? 'btn-danger-solid' : 'btn-primary') + '" data-act="ok">' + escapeHtml(okText) + '</button>' +
                '</div>' +
                '</div>';

            var done = false;

            function finish(result) {
                if (done) {
                    return;
                }
                done = true;
                document.removeEventListener('keydown', onKeydown);
                removeBackdrop(backdrop);
                resolve(result);
            }

            function onKeydown(event) {
                if (event.key === 'Escape') {
                    finish(false);
                }
            }

            backdrop.addEventListener('click', function (event) {
                if (event.target === backdrop) {
                    finish(false);
                }
            });

            backdrop.querySelector('[data-act="cancel"]').addEventListener('click', function () {
                finish(false);
            });
            backdrop.querySelector('[data-act="ok"]').addEventListener('click', function () {
                finish(true);
            });
            document.addEventListener('keydown', onKeydown);

            window.setTimeout(function () {
                var ok = backdrop.querySelector('[data-act="ok"]');
                if (ok) {
                    ok.focus();
                }
            }, 40);
        });
    }

    /* Confirm on submit for declarative forms */
    document.addEventListener(
        'submit',
        function (event) {
            var form = event.target;
            if (!form || !form.matches || !form.matches('form[data-confirm]')) {
                return;
            }
            if (form.dataset.confirmBypass === '1') {
                return;
            }
            event.preventDefault();
            var message = form.getAttribute('data-confirm') || '确认执行此操作？';
            var options = {
                danger: form.hasAttribute('data-confirm-danger'),
                okText: form.getAttribute('data-confirm-ok') || '确认',
                title: form.getAttribute('data-confirm-title') || undefined
            };
            confirmDialog(message, options).then(function (ok) {
                if (!ok) {
                    return;
                }
                form.dataset.confirmBypass = '1';
                if (typeof form.requestSubmit === 'function') {
                    form.requestSubmit();
                } else {
                    form.submit();
                }
                window.setTimeout(function () {
                    delete form.dataset.confirmBypass;
                }, 400);
            });
        },
        true
    );

    /* ---- Lightbox ---- */
    document.addEventListener('click', function (event) {
        var trigger = event.target.closest('[data-lightbox]');
        if (!trigger) {
            return;
        }
        var src = trigger.getAttribute('data-lightbox') || trigger.getAttribute('href') || trigger.getAttribute('src');
        if (!src) {
            return;
        }
        event.preventDefault();

        var backdrop = createBackdrop();
        backdrop.innerHTML =
            '<div class="modal is-lightbox" role="dialog" aria-modal="true" aria-label="截图预览">' +
            '<button type="button" class="modal-close" aria-label="关闭">' +
            '<svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg>' +
            '</button>' +
            '<img class="lightbox-img" src="' + escapeHtml(src) + '" alt="截图预览">' +
            '</div>';

        function close() {
            document.removeEventListener('keydown', onKeydown);
            removeBackdrop(backdrop);
        }

        function onKeydown(keyEvent) {
            if (keyEvent.key === 'Escape') {
                close();
            }
        }

        backdrop.addEventListener('click', function (clickEvent) {
            if (clickEvent.target === backdrop) {
                close();
            }
        });
        backdrop.querySelector('.modal-close').addEventListener('click', close);
        document.addEventListener('keydown', onKeydown);
    });

    /* ---- Detail modal ---- */
    var detailBackdrop = null;

    function closeDetailModal() {
        if (!detailBackdrop) {
            return;
        }
        var node = detailBackdrop;
        detailBackdrop = null;
        if (node._fishDetailKey) {
            document.removeEventListener('keydown', node._fishDetailKey);
        }
        removeBackdrop(node);
    }

    function openDetailModal(url, title) {
        closeDetailModal();

        var backdrop = createBackdrop();
        detailBackdrop = backdrop;
        backdrop.innerHTML =
            '<div class="modal is-detail" role="dialog" aria-modal="true" aria-label="' + escapeHtml(title) + '">' +
            '<div class="modal-head">' +
            '<div class="modal-head-main">' +
            '<div class="modal-title">' + escapeHtml(title) + '</div>' +
            '<div class="modal-text" data-detail-sub>正在加载完整数据…</div>' +
            '</div>' +
            '<button type="button" class="modal-close" aria-label="关闭">' +
            '<svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg>' +
            '</button>' +
            '</div>' +
            '<div class="modal-body" data-detail-body>' +
            '<div class="modal-loading"><span class="spinner"></span>正在加载…</div>' +
            '</div>' +
            '</div>';

        var body = backdrop.querySelector('[data-detail-body]');
        var sub = backdrop.querySelector('[data-detail-sub]');

        function close() {
            if (detailBackdrop === backdrop) {
                closeDetailModal();
            }
        }

        function onKeydown(event) {
            if (event.key === 'Escape' && !document.querySelector('.modal.is-lightbox')) {
                close();
            }
        }

        backdrop._fishDetailKey = onKeydown;
        document.addEventListener('keydown', onKeydown);
        backdrop.addEventListener('click', function (event) {
            if (event.target === backdrop) {
                close();
            }
        });
        backdrop.querySelector('.modal-close').addEventListener('click', close);

        fetch(url, { credentials: 'same-origin', cache: 'no-store' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.text();
            })
            .then(function (html) {
                if (detailBackdrop !== backdrop) {
                    return;
                }
                var doc = new DOMParser().parseFromString(html, 'text/html');
                if (doc.title.indexOf('目标详情') === -1) {
                    throw new Error('目标不存在或已删除');
                }
                var content = doc.querySelector('.content');
                if (!content) {
                    throw new Error('未找到详情内容');
                }
                content.querySelectorAll('.page-header, .flash-stack').forEach(function (el) {
                    el.remove();
                });
                body.innerHTML = content.innerHTML;
                var blocks = body.querySelectorAll('.collection-block').length;
                if (sub) {
                    sub.textContent = blocks ? blocks + ' 条回传记录 · 完整数据' : '暂无回传记录';
                }
                var closeBtn = backdrop.querySelector('.modal-close');
                if (closeBtn) {
                    closeBtn.focus();
                }
            })
            .catch(function (err) {
                if (detailBackdrop !== backdrop) {
                    return;
                }
                if (sub) {
                    sub.textContent = '加载失败';
                }
                body.innerHTML =
                    '<div class="alert alert-danger" role="alert">' +
                    '<svg class="icon" aria-hidden="true"><use href="#i-alert-circle"></use></svg>' +
                    '<div>无法加载完整数据：' + escapeHtml(err.message) + '</div>' +
                    '</div>';
                toast('加载详情失败，请刷新或重新登录', 'error');
            });
    }

    document.addEventListener('click', function (event) {
        var trigger = event.target.closest('[data-detail-url]');
        if (!trigger) {
            return;
        }
        event.preventDefault();
        openDetailModal(
            trigger.getAttribute('data-detail-url'),
            trigger.getAttribute('data-detail-title') || '详情'
        );
    });

    /* ---- Public helpers ---- */
    function setThemePersisted(theme) {
        if (theme === 'light' || theme === 'dark') {
            setTheme(theme, true);
        }
    }

    window.FishUI = {
        toast: toast,
        confirm: confirmDialog,
        setTheme: setThemePersisted,
        getTheme: currentTheme,
        openDetail: openDetailModal
    };
})();
