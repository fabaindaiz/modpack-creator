// The panel's own script: what htmx does not cover. Loaded from /static, never inline (CSP), and
// it never turns a string into code (htmx runs with allowEval: false, and so does this).
(function () {
  'use strict';

  // Errors a route answers with a notice fragment (_notice.html): shown in #notices if HTML.
  var NOTICE_STATUSES = [403, 404, 409, 422, 429, 502, 503, 504];

  function notices() {
    return document.getElementById('notices');
  }

  // Where a notice about a request goes: the slot right after the form that sent it (made if
  // missing), else the fixed corner. A form inside a <dialog> uses the corner: the dialog closes
  // when the request is answered and would hide it.
  function noticeTarget(elt) {
    var form = elt instanceof Element ? elt.closest('form') : null;
    if (!form || form.closest('dialog')) {
      return notices();
    }
    var slot = form.nextElementSibling;
    if (!slot || !slot.hasAttribute('data-form-notice')) {
      slot = document.createElement('div');
      slot.setAttribute('data-form-notice', '');
      slot.className = 'mt-2 space-y-2';
      form.insertAdjacentElement('afterend', slot);
    }
    return slot;
  }

  // Put a copy of a <template> from base.html where the request came from; `extra` is appended as text.
  function showTemplate(id, extra, elt) {
    var target = noticeTarget(elt);
    var template = document.getElementById(id);
    if (!target || !template) {
      return;
    }
    var notice = template.content.firstElementChild.cloneNode(true);
    if (extra) {
      var text = notice.querySelector('p');
      var code = document.createElement('code');
      code.textContent = extra;
      text.appendChild(code);
    }
    target.replaceChildren(notice);
  }

  // Only our notice fragments are HTML; a JSON error (FastAPI's, Starlette's 404 for an unknown
  // path) must never reach innerHTML: it may echo what was submitted.
  function isHtml(xhr) {
    return (xhr.getResponseHeader('Content-Type') || '').toLowerCase().indexOf('text/html') === 0;
  }

  document.addEventListener('htmx:beforeSwap', function (evt) {
    var status = evt.detail.xhr.status;
    var elt = evt.detail.requestConfig ? evt.detail.requestConfig.elt : null;
    if (NOTICE_STATUSES.indexOf(status) !== -1 && !isHtml(evt.detail.xhr)) {
      evt.detail.shouldSwap = false;
      showTemplate('notice-client-error', null, elt);
    } else if (NOTICE_STATUSES.indexOf(status) !== -1 && noticeTarget(elt)) {
      evt.detail.shouldSwap = true;
      evt.detail.target = noticeTarget(elt);
      // The notice replaces the previous one, whatever the triggering element asked for.
      evt.detail.swapOverride = 'innerHTML';
      evt.detail.selectOverride = 'unset';
    } else if (status >= 500) {
      evt.detail.shouldSwap = false;
      showTemplate('notice-server-error', evt.detail.xhr.getResponseHeader('X-Request-ID') || 'sin identificador', elt);
    } else if (status >= 400 && status < 500 && !evt.detail.xhr.getResponseHeader('HX-Redirect')) {
      // Any other client error (a 429 or 400 passed through from Modrinth or Pterodactyl) has no
      // notice fragment: say something, or the click looks like a no-op. A 401 with HX-Redirect
      // is htmx's to follow (to the login).
      evt.detail.shouldSwap = false;
      showTemplate('notice-client-error', null, elt);
    }
  });

  document.addEventListener('htmx:sendError', function (evt) {
    showTemplate('notice-network-error', null, evt.detail.elt);
  });

  // The success of an action (ui.success marks it data-notice-autoclose) closes itself after five
  // seconds; errors, warnings and statuses that only read green (the server in sync, redrawn by a
  // live refresh) stay until closed.
  var CLOSE_AFTER_MS = 5000;
  var AUTOCLOSE = '[data-notice-autoclose]';
  function scheduleClose(node) {
    if (!(node instanceof Element)) {
      return;
    }
    var found = node.matches(AUTOCLOSE) ? [node] : [];
    node.querySelectorAll(AUTOCLOSE).forEach(function (n) {
      found.push(n);
    });
    found.forEach(function (notice) {
      window.setTimeout(function () {
        notice.remove();
      }, CLOSE_AFTER_MS);
    });
  }
  new MutationObserver(function (records) {
    records.forEach(function (record) {
      record.addedNodes.forEach(scheduleClose);
    });
  }).observe(document.body, { childList: true, subtree: true });
  // The page may be drawn with one (after a redirect, "Modpack vinculado"): no mutation adds it.
  scheduleClose(document.body);

  document.addEventListener('click', function (evt) {
    var element = evt.target instanceof Element ? evt.target : null;
    if (!element) {
      return;
    }
    var close = element.closest('[data-notice-close]');
    if (close) {
      var notice = close.closest('[data-notice]');
      if (notice) {
        notice.remove();
      }
      return;
    }
    var opener = element.closest('[data-dialog-open]');
    if (opener) {
      var dialog = document.getElementById(opener.getAttribute('data-dialog-open'));
      if (dialog && typeof dialog.showModal === 'function') {
        dialog.showModal();
      }
      return;
    }
    var closer = element.closest('[data-dialog-close]');
    if (closer) {
      var parent = closer.closest('dialog');
      if (parent) {
        parent.close();
      }
    }
  });

  // The side panel (base.html) for a mod's card. A swap into its body opens it; whatever opened it
  // gets the focus back when it closes (Escape and Cerrar both close it, natively and above).
  var drawer = document.getElementById('mod-drawer');
  var drawerOpener = null;

  // A ctrl, cmd or shift click on a mod link, or a click with another button, is for the browser:
  // follow the href (a new tab or window). htmx cancels the click of any link it drives, and only
  // skips ctrl/cmd on boosted ones, so this stops the click in the capture phase, before htmx's
  // listener on the link runs, and leaves the default action alone. The middle button fires
  // auxclick, which htmx does not listen to: it needs nothing here.
  document.addEventListener('click', function (evt) {
    var link = evt.target instanceof Element ? evt.target.closest('[data-mod-link]') : null;
    if (link && (evt.ctrlKey || evt.metaKey || evt.shiftKey || evt.button !== 0)) {
      evt.stopImmediatePropagation();
    }
  }, true);

  if (drawer) {
    document.addEventListener('htmx:afterSwap', function (evt) {
      if (!evt.detail.target || evt.detail.target.id !== 'mod-drawer-body') {
        return;
      }
      if (!drawer.open) {
        // Only the first link remembers: a click inside the panel replaces the card, and the
        // link it came from is gone with it.
        drawerOpener = evt.detail.elt instanceof HTMLElement ? evt.detail.elt : null;
        drawer.showModal();
      }
      evt.detail.target.scrollTop = 0;
    });
    // A click on the backdrop (the dialog itself, outside its content) closes it.
    drawer.addEventListener('click', function (evt) {
      if (evt.target === drawer) {
        drawer.close();
      }
    });
    drawer.addEventListener('close', function () {
      var body = document.getElementById('mod-drawer-body');
      if (body) {
        body.replaceChildren();
      }
      if (drawerOpener && document.body.contains(drawerOpener)) {
        drawerOpener.focus();
      }
      drawerOpener = null;
    });
  }

  // A form inside a confirmation dialog closes it once its request is answered, also on an
  // error: the modal would otherwise hide the notice that explains it. Only the form: what the
  // dialog loads for itself (the update preview when it opens, the build select) leaves it open.
  document.addEventListener('htmx:afterRequest', function (evt) {
    var elt = evt.detail.elt;
    var dialog = elt instanceof HTMLFormElement ? elt.closest('dialog') : null;
    if (dialog && dialog.open) {
      dialog.close();
    }
  });

  // On a narrow screen the section nav scrolls sideways: keep the current section in view.
  var current = document.querySelector('nav[aria-label="Secciones"] [aria-current="page"]');
  if (current) {
    var strip = current.closest('nav');
    if (strip) {
      var over = current.getBoundingClientRect().right - strip.getBoundingClientRect().right;
      if (over > 0) {
        strip.scrollLeft += over + 8;
      }
    }
  }
})();
