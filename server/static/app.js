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
            if (event.key === 'Escape' && !document.querySelector('.modal.is-lightbox') &&
                !document.querySelector('[data-modal-layer]')) {
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

    /* ---- Config tabs ---- */
    document.querySelectorAll('[data-tab-group]').forEach(function (group) {
        var buttons = Array.prototype.slice.call(group.querySelectorAll('[data-tab]'));
        var panels = Array.prototype.slice.call(document.querySelectorAll('[data-tab-panel]'));
        if (!buttons.length || !panels.length) {
            return;
        }
        var storageKey = 'fish-tab-' + (group.getAttribute('data-tab-group') || 'default');

        function activate(name, persist) {
            var known = buttons.some(function (btn) {
                return btn.getAttribute('data-tab') === name;
            });
            if (!known) {
                name = buttons[0].getAttribute('data-tab');
            }
            buttons.forEach(function (btn) {
                var active = btn.getAttribute('data-tab') === name;
                btn.classList.toggle('is-active', active);
                btn.setAttribute('aria-selected', active ? 'true' : 'false');
            });
            panels.forEach(function (panel) {
                panel.hidden = panel.getAttribute('data-tab-panel') !== name;
            });
            if (persist) {
                try {
                    window.localStorage.setItem(storageKey, name);
                } catch (e) {
                    /* ignore */
                }
                if (window.history && window.history.replaceState) {
                    window.history.replaceState(null, '', '#' + name);
                }
            }
        }

        buttons.forEach(function (btn) {
            btn.addEventListener('click', function () {
                activate(btn.getAttribute('data-tab'), true);
            });
        });

        var initial = (window.location.hash || '').replace('#', '');
        if (!initial) {
            try {
                initial = window.localStorage.getItem(storageKey) || '';
            } catch (e) {
                initial = '';
            }
        }
        activate(initial || buttons[0].getAttribute('data-tab'), false);
    });

    /* ---- Callback table + record detail modals ---- */
    function isTopModalLayer(node) {
        var layers = document.querySelectorAll('[data-modal-layer]');
        return layers.length > 0 && layers[layers.length - 1] === node;
    }

    function openCollectionsModal(url, title) {
        var backdrop = createBackdrop();
        backdrop.setAttribute('data-modal-layer', 'collections');
        backdrop.innerHTML =
            '<div class="modal is-detail is-callbacks" role="dialog" aria-modal="true" aria-label="' + escapeHtml(title) + '">' +
            '<div class="modal-head">' +
            '<div class="modal-head-main">' +
            '<div class="modal-title">' + escapeHtml(title) + '</div>' +
            '<div class="modal-text" data-col-sub>正在加载回传记录…</div>' +
            '</div>' +
            '<button type="button" class="modal-close" aria-label="关闭">' +
            '<svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg>' +
            '</button>' +
            '</div>' +
            '<div class="modal-body" data-col-body>' +
            '<div class="modal-loading"><span class="spinner"></span>正在加载…</div>' +
            '</div>' +
            '</div>';

        var body = backdrop.querySelector('[data-col-body]');
        var sub = backdrop.querySelector('[data-col-sub]');

        function close() {
            document.removeEventListener('keydown', onKeydown);
            removeBackdrop(backdrop);
        }

        function onKeydown(event) {
            if (event.key === 'Escape' && isTopModalLayer(backdrop) &&
                !document.querySelector('.modal.is-lightbox')) {
                close();
            }
        }

        function load(targetUrl) {
            body.innerHTML =
                '<div class="modal-loading"><span class="spinner"></span>正在加载…</div>';
            fetch(targetUrl, { credentials: 'same-origin', cache: 'no-store' })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status);
                    }
                    return response.text();
                })
                .then(function (html) {
                    if (!backdrop.parentNode) {
                        return;
                    }
                    body.innerHTML = html;
                    var fragment = body.querySelector('[data-collections-fragment]');
                    var total = fragment ? fragment.getAttribute('data-collections-total') : '0';
                    sub.textContent = total === '0'
                        ? '暂无回传记录'
                        : total + ' 条回传记录 · 点击「详情」查看完整数据';
                })
                .catch(function (err) {
                    body.innerHTML =
                        '<div class="alert alert-danger" role="alert">' +
                        '<svg class="icon" aria-hidden="true"><use href="#i-alert-circle"></use></svg>' +
                        '<div>无法加载回传记录：' + escapeHtml(err.message) + '</div>' +
                        '</div>';
                    sub.textContent = '加载失败';
                });
        }

        backdrop.querySelector('.modal-close').addEventListener('click', close);
        document.addEventListener('keydown', onKeydown);
        backdrop.addEventListener('click', function (event) {
            if (event.target === backdrop) {
                close();
                return;
            }
            var pageLink = event.target.closest('[data-col-page]');
            if (pageLink) {
                event.preventDefault();
                var page = pageLink.getAttribute('data-col-page');
                var size = pageLink.getAttribute('data-col-size') || '';
                load(url + '?page=' + encodeURIComponent(page) +
                    '&page_size=' + encodeURIComponent(size));
                return;
            }
            var recordBtn = event.target.closest('[data-collection-url]');
            if (recordBtn) {
                event.preventDefault();
                openCollectionDetailModal(
                    recordBtn.getAttribute('data-collection-url'),
                    recordBtn.getAttribute('data-collection-title') || '回传详情'
                );
            }
        });
        backdrop.addEventListener('change', function (event) {
            if (event.target.matches('[data-col-page-size]')) {
                load(url + '?page=1&page_size=' + encodeURIComponent(event.target.value));
            }
        });

        load(url);
    }

    function openCollectionDetailModal(url, title) {
        var backdrop = createBackdrop();
        backdrop.setAttribute('data-modal-layer', 'collection-detail');
        backdrop.innerHTML =
            '<div class="modal is-detail is-collection-detail" role="dialog" aria-modal="true" aria-label="' + escapeHtml(title) + '">' +
            '<div class="modal-head">' +
            '<div class="modal-head-main">' +
            '<div class="modal-title">回传详情</div>' +
            '<div class="modal-text" data-record-sub>' + escapeHtml(title) + '</div>' +
            '</div>' +
            '<button type="button" class="modal-close" aria-label="关闭">' +
            '<svg class="icon" aria-hidden="true"><use href="#i-x"></use></svg>' +
            '</button>' +
            '</div>' +
            '<div class="modal-body" data-record-body>' +
            '<div class="modal-loading"><span class="spinner"></span>正在加载…</div>' +
            '</div>' +
            '</div>';

        var body = backdrop.querySelector('[data-record-body]');
        var sub = backdrop.querySelector('[data-record-sub]');

        function close() {
            document.removeEventListener('keydown', onKeydown);
            removeBackdrop(backdrop);
        }

        function onKeydown(event) {
            if (event.key === 'Escape' && isTopModalLayer(backdrop) &&
                !document.querySelector('.modal.is-lightbox')) {
                close();
            }
        }

        backdrop.querySelector('.modal-close').addEventListener('click', close);
        backdrop.addEventListener('click', function (event) {
            if (event.target === backdrop) {
                close();
            }
        });
        document.addEventListener('keydown', onKeydown);

        fetch(url, { credentials: 'same-origin', cache: 'no-store' })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                return response.text();
            })
            .then(function (html) {
                if (!backdrop.parentNode) {
                    return;
                }
                body.innerHTML = html;
                var fragment = body.querySelector('[data-detail-sub]');
                if (fragment) {
                    sub.textContent = fragment.getAttribute('data-detail-sub');
                }
            })
            .catch(function (err) {
                body.innerHTML =
                    '<div class="alert alert-danger" role="alert">' +
                    '<svg class="icon" aria-hidden="true"><use href="#i-alert-circle"></use></svg>' +
                    '<div>无法加载回传详情：' + escapeHtml(err.message) + '</div>' +
                    '</div>';
                sub.textContent = '加载失败';
            });
    }

    document.addEventListener('click', function (event) {
        var trigger = event.target.closest('[data-collections-url]');
        if (!trigger) {
            return;
        }
        event.preventDefault();
        openCollectionsModal(
            trigger.getAttribute('data-collections-url'),
            trigger.getAttribute('data-collections-title') || '回传记录'
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
