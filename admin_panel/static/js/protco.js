/*
 * ProtCo shared admin script
 * - session guard + logout (works on every admin page)
 * - live notifications (bell), messages (envelope), unread red badges
 * - logged-in user's name / photo in the profile avatar
 * - tiny localStorage "database" shared with the user-side pages
 *   (user_concern.html, user_logged.html, user_register.html)
 *
 * NOTE: localStorage is shared only between pages on the SAME origin
 * (same domain + port). If the user-side site lives on another domain,
 * these keys must be replaced by a real backend API.
 */
(function () {
    "use strict";

    var root = document.documentElement;
    var LOGIN_URL = root.getAttribute("data-login-url") || "/login/";

    var K = {
        session: "protco_active_session",
        users: "protco_data_users",
        reports: "protco_data_reports",
        concerns: "protco_data_concerns",
        notifs: "protco_data_notifications",
        sidebar: "protco_sidebar_collapsed",
        loggedOut: "protco_just_logged_out",
    };

    function parse(raw, fb) {
        try {
            var v = JSON.parse(raw);
            return v == null ? fb : v;
        } catch (e) {
            return fb;
        }
    }
    function lsGet(k) {
        try {
            return localStorage.getItem(k);
        } catch (e) {
            return null;
        }
    }
    function getSession() {
        var s = parse(lsGet(K.session), null);
        return s && typeof s === "object" ? s : null;
    }

    // ---- SESSION GUARD (runs immediately, before the page paints) ----
    if (!getSession()) {
        root.style.visibility = "hidden";
        window.Protco = { redirecting: true, ready: function () {} };
        location.replace(LOGIN_URL);
        return;
    }

    // remember sidebar state (applied before the page paints, so there is no flicker)
    if (lsGet(K.sidebar) === "1") root.classList.add("sidebar-collapsed");

    // ---- helpers ----
    function list(k) {
        var v = parse(lsGet(k), []);
        return Array.isArray(v) ? v : [];
    }
    function save(k, arr) {
        try {
            localStorage.setItem(k, JSON.stringify(arr));
        } catch (e) {}
    }
    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
            return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
        });
    }
    function nextId(arr) {
        return (
            arr.reduce(function (m, x) {
                return Math.max(m, Number(x.id) || 0);
            }, 0) + 1
        );
    }
    function timeAgo(iso) {
        var t = new Date(iso).getTime();
        if (!t) return "";
        var s = Math.max(0, Math.floor((Date.now() - t) / 1000));
        if (s < 45) return "just now";
        var m = Math.floor(s / 60);
        if (m < 60) return m + (m === 1 ? " min ago" : " mins ago");
        var h = Math.floor(m / 60);
        if (h < 24) return h + (h === 1 ? " hr ago" : " hrs ago");
        var d = Math.floor(h / 24);
        if (d < 30) return d + (d === 1 ? " day ago" : " days ago");
        return new Date(iso).toLocaleDateString();
    }
    function fullName(s) {
        s = s || {};
        var n = [s.firstName, s.lastName, s.suffix].filter(Boolean).join(" ").trim();
        return n || s.name || s.username || "Admin";
    }
    function initialOf(s) {
        return fullName(s).charAt(0).toUpperCase() || "A";
    }
    function $(sel, ctx) {
        return (ctx || document).querySelector(sel);
    }
    function $$(sel, ctx) {
        return Array.prototype.slice.call((ctx || document).querySelectorAll(sel));
    }

    // ---- public db ----
    var db = {
        users: function () {
            return list(K.users);
        },
        reports: function () {
            return list(K.reports);
        },
        concerns: function () {
            return list(K.concerns);
        },
        notifs: function () {
            return list(K.notifs);
        },
        saveUsers: function (a) {
            save(K.users, a);
            refresh();
        },
        saveReports: function (a) {
            save(K.reports, a);
            refresh();
        },
        saveConcerns: function (a) {
            save(K.concerns, a);
            refresh();
        },
        saveNotifs: function (a) {
            save(K.notifs, a);
            refresh();
        },
    };

    // ---- toast ----
    function toast(message, opts) {
        opts = opts || {};
        var stack = $("#toastStack");
        if (!stack) return;
        var icon =
            opts.icon ||
            (opts.type === "success" ? "ph-check-circle" : opts.type === "warn" ? "ph-warning" : "ph-bell-ringing");
        var el = document.createElement("div");
        el.className = "toast " + (opts.type || "");
        el.innerHTML =
            '<i class="ph ' +
            icon +
            '"></i><div>' +
            (opts.title ? "<strong>" + esc(opts.title) + "</strong>" : "") +
            "<span>" +
            esc(message) +
            "</span></div>";
        stack.appendChild(el);
        setTimeout(function () {
            el.classList.add("out");
            setTimeout(function () {
                el.remove();
            }, 320);
        }, opts.duration || 4200);
    }

    // ---- logout ----
    function openLogout() {
        var m = $("#logoutModal");
        if (m) m.classList.add("open");
    }
    function closeLogout() {
        var m = $("#logoutModal");
        if (m) m.classList.remove("open");
    }
    function logout() {
        try {
            localStorage.removeItem(K.session);
        } catch (e) {}
        try {
            sessionStorage.setItem(K.loggedOut, "1");
        } catch (e) {} // login page shows the "You logged out" notice
        location.replace(LOGIN_URL);
    }

    // ---- rendering ----
    var known = null; // ids of notifications already seen (for toast on new ones)
    var lastSnap = "";

    function setBadge(id, n) {
        var b = document.getElementById(id);
        if (!b) return;
        var prev = b.getAttribute("data-n");
        b.hidden = n <= 0;
        b.textContent = n > 99 ? "99+" : String(n);
        b.setAttribute("data-n", n);
        if (prev !== null && Number(prev) < n) {
            b.classList.remove("pop");
            void b.offsetWidth;
            b.classList.add("pop");
        }
    }
    function ring(btnId) {
        var b = document.getElementById(btnId);
        if (!b) return;
        b.classList.remove("ring");
        void b.offsetWidth;
        b.classList.add("ring");
    }

    function notifIcon(type) {
        return type === "report"
            ? ["report", "ph-file-text"]
            : type === "user"
              ? ["user", "ph-user-plus"]
              : ["", "ph-chat-circle-text"];
    }

    function renderNotifs(notifs) {
        var box = $("#notifList");
        if (!box) return;
        var sorted = notifs
            .slice()
            .sort(function (a, b) {
                return new Date(b.createdAt) - new Date(a.createdAt);
            })
            .slice(0, 30);
        var unread = notifs.filter(function (n) {
            return !n.read;
        }).length;
        setBadge("notifBadge", unread);
        var hc = $("#notifCount");
        if (hc) hc.textContent = unread ? unread + " unread" : "All caught up";
        if (!sorted.length) {
            box.innerHTML = '<div class="dd-empty"><i class="ph ph-bell-slash"></i>No notifications yet</div>';
            return;
        }
        box.innerHTML = sorted
            .map(function (n) {
                var ic = notifIcon(n.type);
                return (
                    '<button type="button" class="dd-item ' +
                    (n.read ? "" : "unread") +
                    '" data-notif-id="' +
                    esc(n.id) +
                    '">' +
                    '<div class="dd-ico ' +
                    ic[0] +
                    '"><i class="ph ' +
                    ic[1] +
                    '"></i></div>' +
                    '<div class="dd-body"><span class="dd-title">' +
                    esc(n.title) +
                    '</span><span class="dd-text">' +
                    esc(n.text) +
                    '</span><span class="dd-time">' +
                    timeAgo(n.createdAt) +
                    "</span></div>" +
                    (n.read ? "" : '<span class="dd-dot"></span>') +
                    "</button>"
                );
            })
            .join("");
    }

    function renderMessages(concerns) {
        var box = $("#msgList");
        if (!box) return;
        var sorted = concerns
            .slice()
            .sort(function (a, b) {
                return new Date(b.createdAt) - new Date(a.createdAt);
            })
            .slice(0, 30);
        var unread = concerns.filter(function (c) {
            return !c.read;
        }).length;
        setBadge("msgBadge", unread);
        var hc = $("#msgCount");
        if (hc) hc.textContent = unread ? unread + " unread" : "All read";
        if (!sorted.length) {
            box.innerHTML = '<div class="dd-empty"><i class="ph ph-envelope-open"></i>No messages yet</div>';
            return;
        }
        box.innerHTML = sorted
            .map(function (c) {
                return (
                    '<button type="button" class="dd-item ' +
                    (c.read ? "" : "unread") +
                    '" data-msg-id="' +
                    esc(c.id) +
                    '">' +
                    '<div class="dd-ico"><i class="ph ph-envelope-simple"></i></div>' +
                    '<div class="dd-body"><span class="dd-title">' +
                    esc(c.name || "Anonymous") +
                    (c.subject ? " &middot; " + esc(c.subject) : "") +
                    "</span>" +
                    '<span class="dd-text">' +
                    esc(c.message) +
                    '</span><span class="dd-time">' +
                    timeAgo(c.createdAt) +
                    "</span></div>" +
                    (c.read ? "" : '<span class="dd-dot"></span>') +
                    "</button>"
                );
            })
            .join("");
    }

    function renderProfile() {
        var s = getSession() || {};
        var name = fullName(s);
        var avatarHtml = s.photo ? '<img src="' + esc(s.photo) + '" alt="">' : esc(initialOf(s));
        $$('[data-bind="avatar"]').forEach(function (el) {
            el.innerHTML = avatarHtml;
        });
        $$('[data-bind="name"]').forEach(function (el) {
            el.textContent = s.firstName || name;
        });
        $$('[data-bind="fullname"]').forEach(function (el) {
            el.textContent = name;
        });
        $$('[data-bind="role"]').forEach(function (el) {
            el.textContent = s.role || "Admin";
        });
        $$('[data-bind="contact"]').forEach(function (el) {
            el.textContent = s.contact ? "+63 " + s.contact : "No contact number";
        });
    }

    function refresh(initial) {
        var data = { users: db.users(), reports: db.reports(), concerns: db.concerns(), notifs: db.notifs() };

        // detect brand-new notifications -> toast + bell ring
        var ids = data.notifs.map(function (n) {
            return String(n.id);
        });
        if (known === null) {
            known = ids;
        } else {
            var fresh = data.notifs.filter(function (n) {
                return known.indexOf(String(n.id)) === -1 && !n.read;
            });
            if (fresh.length) {
                fresh.slice(-3).forEach(function (n) {
                    toast(n.text, { title: n.title });
                });
                ring("notifBtn");
                if (
                    fresh.some(function (n) {
                        return n.type === "concern";
                    })
                )
                    ring("msgBtn");
            }
            known = ids;
        }

        renderNotifs(data.notifs);
        renderMessages(data.concerns);
        renderProfile();
        document.dispatchEvent(new CustomEvent("protco:data", { detail: data }));
    }

    function snapshot() {
        return [K.session, K.users, K.reports, K.concerns, K.notifs].map(lsGet).join("\u0001");
    }

    // ---- actions ----
    function markNotif(id) {
        var a = db.notifs();
        a.forEach(function (n) {
            if (String(n.id) === String(id)) n.read = true;
        });
        save(K.notifs, a);
        refresh();
    }
    function markAllNotifs() {
        var a = db.notifs();
        a.forEach(function (n) {
            n.read = true;
        });
        save(K.notifs, a);
        refresh();
    }
    function openMessage(id) {
        var a = db.concerns();
        var c = a.filter(function (x) {
            return String(x.id) === String(id);
        })[0];
        if (!c) return;
        if (!c.read) {
            c.read = true;
            save(K.concerns, a);
            refresh();
        }
        $("#msgFrom").textContent = c.name || "Anonymous";
        $("#msgEmail").textContent = (c.email || "No email") + " · " + timeAgo(c.createdAt);
        $("#msgSubject").textContent = c.subject || "Concern";
        $("#msgText").textContent = c.message || "";
        $("#msgAvatar").textContent = (c.name || "A").charAt(0).toUpperCase();
        $("#messageModal").classList.add("open");
        closeDropdowns();
    }
    function markAllMsgs() {
        var a = db.concerns();
        a.forEach(function (c) {
            c.read = true;
        });
        save(K.concerns, a);
        refresh();
    }

    function closeDropdowns(except) {
        $$(".dropdown-box.open").forEach(function (d) {
            if (d !== except) d.classList.remove("open");
        });
        $$("[data-dd-toggle].open").forEach(function (b) {
            if (!except || b.getAttribute("data-dd-toggle") !== except.id) b.classList.remove("open");
        });
    }
    function toggleDropdown(btn) {
        var dd = document.getElementById(btn.getAttribute("data-dd-toggle"));
        if (!dd) return;
        var willOpen = !dd.classList.contains("open");
        closeDropdowns(willOpen ? dd : null);
        dd.classList.toggle("open", willOpen);
        btn.classList.toggle("open", willOpen);
    }

    // ---- sidebar collapse (stays closed until the button is pressed again) ----
    function setCollapsed(on) {
        root.classList.toggle("sidebar-collapsed", on);
        try {
            localStorage.setItem(K.sidebar, on ? "1" : "0");
        } catch (e) {}
        var b = $(".collapse-btn");
        if (b) b.setAttribute("data-label", on ? "Expand sidebar" : "Collapse sidebar");
        hideTip();
    }

    // ---- tooltip for icons while the sidebar is collapsed ----
    var tip = null;
    function showTip(el) {
        if (!root.classList.contains("sidebar-collapsed") || window.innerWidth <= 960) return;
        if (!tip) {
            tip = document.createElement("div");
            tip.className = "nav-tip";
            document.body.appendChild(tip);
        }
        var r = el.getBoundingClientRect();
        tip.textContent = el.getAttribute("data-label");
        tip.style.left = r.right + 12 + "px";
        tip.style.top = r.top + r.height / 2 - tip.offsetHeight / 2 + "px";
        tip.classList.add("show");
        tip.style.top = r.top + r.height / 2 - tip.offsetHeight / 2 + "px";
    }
    function hideTip() {
        if (tip) tip.classList.remove("show");
    }

    // ---- demo data (for previewing the UI) ----
    function iso(minAgo) {
        return new Date(Date.now() - minAgo * 60000).toISOString();
    }
    function seedDemo() {
        save(K.users, [
            {
                id: 1,
                firstName: "Camilla",
                lastName: "Acosta",
                email: "camilla@protco.edu",
                age: 21,
                role: "Student",
                status: "active",
                createdAt: iso(60 * 50),
            },
            {
                id: 2,
                firstName: "John",
                lastName: "Marlo",
                email: "marlo@protco.edu",
                age: 28,
                role: "Teacher",
                status: "active",
                createdAt: iso(60 * 30),
            },
            {
                id: 3,
                firstName: "Rhea",
                lastName: "Santos",
                email: "rhea@protco.edu",
                age: 17,
                role: "Student",
                status: "active",
                createdAt: iso(60 * 5),
            },
        ]);
        save(K.reports, [
            {
                id: 1,
                title: "Cracked wall - Grade 4 room",
                description: "Diagonal crack near the window frame.",
                reporter: "Camilla Acosta",
                reporterId: 1,
                status: "Pending",
                createdAt: iso(60 * 20),
            },
            {
                id: 2,
                title: "Roof leak #4",
                description: "Water drips on the back row during rain.",
                reporter: "John Marlo",
                reporterId: 2,
                status: "Accepted",
                createdAt: iso(60 * 8),
            },
        ]);
        save(K.concerns, [
            {
                id: 1,
                name: "Rhea Santos",
                email: "rhea@protco.edu",
                subject: "Broken window",
                message: "The window in Room 3 will not close properly and rain comes in.",
                createdAt: iso(25),
                read: false,
            },
            {
                id: 2,
                name: "John Marlo",
                email: "marlo@protco.edu",
                subject: "Exposed wires",
                message: "There are exposed wires near the faculty room outlet.",
                createdAt: iso(180),
                read: true,
            },
        ]);
        save(K.notifs, [
            {
                id: 1,
                type: "concern",
                title: "New concern",
                text: "Rhea Santos sent a concern: Broken window",
                createdAt: iso(25),
                read: false,
            },
            {
                id: 2,
                type: "report",
                title: "New report logged",
                text: "Roof leak #4 by John Marlo",
                createdAt: iso(60 * 8),
                read: true,
            },
        ]);
        refresh();
    }
    function clearData() {
        [K.users, K.reports, K.concerns, K.notifs].forEach(function (k) {
            try {
                localStorage.removeItem(k);
            } catch (e) {}
        });
        refresh();
    }

    // ---- watermark tile (settings / weather / school map pages) ----
    function buildWatermarks() {
        $$(".wm-card").forEach(function (card) {
            var name = card.getAttribute("data-owner") || "";
            var layer = $(".wm-layer", card);
            if (!layer || !name) return;
            var label = name
                .split(",")
                .map(function (x) {
                    return x.trim();
                })
                .filter(Boolean)
                .join("  \u2022  ");
            var safe = label.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
            var w = Math.round(label.length * 17 + 80);
            var svg =
                '<svg xmlns="http://www.w3.org/2000/svg" width="' +
                w +
                '" height="150">' +
                '<text x="20" y="80" font-family="Arial, sans-serif" font-size="26" font-weight="800" fill="#2563eb" fill-opacity="0.11">' +
                safe +
                "</text></svg>";
            layer.style.backgroundImage = 'url("data:image/svg+xml;utf8,' + encodeURIComponent(svg) + '")';
        });
    }

    // ---- events ----
    document.addEventListener("click", function (e) {
        var t = e.target;
        var tog = t.closest("[data-dd-toggle]");
        if (tog) {
            toggleDropdown(tog);
            return;
        }

        var act = t.closest("[data-action]");
        if (act) {
            var a = act.getAttribute("data-action");
            if (a === "open-logout") openLogout();
            else if (a === "close-logout") closeLogout();
            else if (a === "confirm-logout") logout();
            else if (a === "mark-all-notifs") markAllNotifs();
            else if (a === "mark-all-msgs") markAllMsgs();
            else if (a === "close-message") $("#messageModal").classList.remove("open");
            else if (a === "collapse-sidebar") setCollapsed(!root.classList.contains("sidebar-collapsed"));
            else if (a === "toggle-sidebar") document.body.classList.toggle("sidebar-open");
            else if (a === "soon")
                toast("This feature is coming soon.", { title: "Profile", icon: "ph-hourglass-medium" });
            return;
        }

        var n = t.closest("[data-notif-id]");
        if (n) {
            markNotif(n.getAttribute("data-notif-id"));
            return;
        }
        var m = t.closest("[data-msg-id]");
        if (m) {
            openMessage(m.getAttribute("data-msg-id"));
            return;
        }

        if (t.classList.contains("modal-overlay")) t.classList.remove("open");
        if (t.classList.contains("sidebar-backdrop")) document.body.classList.remove("sidebar-open");
        if (!t.closest(".dd-wrap")) closeDropdowns();
    });

    document.addEventListener("mouseover", function (e) {
        var el = e.target.closest ? e.target.closest(".sidebar [data-label]") : null;
        if (el) showTip(el);
    });
    document.addEventListener("mouseout", function (e) {
        if (e.target.closest && e.target.closest(".sidebar [data-label]")) hideTip();
    });

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            closeDropdowns();
            $$(".modal-overlay.open").forEach(function (m) {
                m.classList.remove("open");
            });
            document.body.classList.remove("sidebar-open");
        }
    });

    // another tab changed data / logged out
    window.addEventListener("storage", function (e) {
        if (e.key === K.session && !e.newValue) {
            location.replace(LOGIN_URL);
            return;
        }
        lastSnap = snapshot();
        refresh();
    });
    // back/forward cache: re-check the session after logout
    window.addEventListener("pageshow", function (e) {
        if (e.persisted && !getSession()) location.replace(LOGIN_URL);
    });

    function init() {
        // ?demo=1 seeds sample data so the UI can be previewed (remove before production)
        if (/[?&]demo=1\b/.test(location.search)) {
            if (!db.users().length && !db.reports().length && !db.concerns().length) seedDemo();
            try {
                history.replaceState(null, "", location.pathname);
            } catch (e) {}
        }
        var cb = $(".collapse-btn");
        if (cb)
            cb.setAttribute(
                "data-label",
                root.classList.contains("sidebar-collapsed") ? "Expand sidebar" : "Collapse sidebar",
            );
        buildWatermarks();
        refresh(true);
        lastSnap = snapshot();
        setInterval(function () {
            var s = snapshot();
            if (!getSession()) {
                location.replace(LOGIN_URL);
                return;
            }
            if (s !== lastSnap) {
                lastSnap = s;
                refresh();
            } else {
                renderNotifs(db.notifs());
                renderMessages(db.concerns());
            } // keep "x min ago" fresh
        }, 3000);
        root.style.visibility = "";
    }

    var readyQueue = [],
        isReady = false;
    window.Protco = {
        K: K,
        db: db,
        esc: esc,
        timeAgo: timeAgo,
        nextId: nextId,
        toast: toast,
        getSession: getSession,
        fullName: fullName,
        logout: logout,
        seedDemo: seedDemo,
        clearData: clearData,
        ready: function (fn) {
            if (isReady) fn();
            else readyQueue.push(fn);
        },
    };

    document.addEventListener("DOMContentLoaded", function () {
        init();
        isReady = true;
        readyQueue.forEach(function (fn) {
            fn();
        });
    });
})();
