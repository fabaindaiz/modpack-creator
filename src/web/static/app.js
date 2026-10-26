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

  // A mod's row selects apply on change (A1): htmx swaps nothing for an error, so the select would
  // show a value that was not saved. It goes back to the one it was drawn with.
  function restoreSaved(elt) {
    if (!elt || !elt.querySelectorAll) return;
    var selects = elt.matches && elt.matches('select[data-saved]') ? [elt] : elt.querySelectorAll('select[data-saved]');
    Array.prototype.forEach.call(selects, function (select) { select.value = select.dataset.saved; });
  }

  // A job card (A13, data-job-detail) loads its detail when it opens and lets it go when it closes: it polls
  // while the job runs, and a closed card must not keep it polling. `toggle` does not bubble.
  document.addEventListener('toggle', function (evt) {
    var card = evt.target;
    if (!(card instanceof HTMLDetailsElement) || !card.dataset.jobDetail) return;
    var slot = document.getElementById('job-detail-' + card.dataset.job);
    if (!slot) return;
    if (card.open) {
      htmx.ajax('GET', card.dataset.jobDetail, {target: slot, swap: 'innerHTML'});
    } else {
      slot.innerHTML = '';
    }
  }, true);

  // A row change can be saved and still fail after (the resolution that follows it): the select put
  // back would then show what is no longer saved. Asking #mods again shows what is.
  function redrawMods() {
    if (!document.getElementById('mods')) return;
    htmx.ajax('GET', location.pathname + location.search, {target: '#mods', select: '#mods', swap: 'outerHTML'});
  }

  document.addEventListener('htmx:responseError', function (evt) {
    var elt = evt.detail.elt;
    restoreSaved(elt);
    if (elt instanceof Element && elt.closest('#mods') && elt.querySelector('select[data-saved]')) redrawMods();
  });

  // #pack-main reloads itself when a job starts or ends (detail.html) with a bare GET of the pack:
  // the page's own query (the mod list's filters, the tab) goes with it.
  document.addEventListener('htmx:configRequest', function (evt) {
    if (evt.detail.verb !== 'get' || !(evt.detail.elt instanceof Element) || evt.detail.elt.id !== 'pack-main') return;
    new URLSearchParams(location.search).forEach(function (value, name) {
      evt.detail.parameters[name] = value;
    });
  });

  // A pin that goes back opens #pin-dialog (F27): cancelling it leaves the row's select on what is
  // saved; confirming redraws #mods anyway. `close` does not bubble: caught on the way down.
  document.addEventListener('close', function (evt) {
    if (evt.target instanceof Element && evt.target.closest('#pin-dialog')) {
      restoreSaved(document.getElementById('mods'));
    }
  }, true);

  document.addEventListener('htmx:sendError', function (evt) {
    restoreSaved(evt.detail.elt);
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
    // "Todos al servidor" / "Desactivar todos" (Por decidir): mark every select of the button's
    // form with its value. Nothing is sent: the form's own button saves.
    var setAll = element.closest('[data-set-all]');
    if (setAll) {
      var form = setAll.closest('form');
      if (form) {
        form.querySelectorAll('select').forEach(function (select) {
          select.value = setAll.getAttribute('data-set-all');
        });
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
        // link it came from is gone with it. afterSwap's own `elt` is the target (the panel's
        // body): the link is the request config's.
        var link = evt.detail.requestConfig ? evt.detail.requestConfig.elt : null;
        drawerOpener = link instanceof HTMLElement ? link : null;
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

  // A dialog the server sends to be opened (data-autoopen; the build dialog) comes drawn closed:
  // it opens here as a modal, with showModal() (the page inert, Escape, the focus inside, on its
  // autofocus title). The mark is taken off so a later swap does not open it again, and an open
  // dialog is left as it is (showModal() on it throws). When it closes, the focus goes back to what
  // opened it, or to its data-return-focus element when that is gone (the "Crear build" button,
  // redrawn with the builds: the new one, by its id). A dialog that replaces another (an action inside the build dialog,
  // answered with the dialog recalculated) keeps the first one's opener.
  var openers = new WeakMap();

  function returnFocus(dialog) {
    var opener = openers.get(dialog);
    var target = opener && opener.isConnected ? opener
      : document.getElementById(dialog.getAttribute('data-return-focus') || '');
    if (target) {
      target.focus();
    }
  }

  document.addEventListener('htmx:afterSwap', function (evt) {
    var target = evt.detail.target;
    if (!(target instanceof Element)) {
      return;
    }
    var found = Array.prototype.slice.call(target.querySelectorAll('dialog[data-autoopen]'));
    if (target.matches('dialog[data-autoopen]')) {
      found.unshift(target);
    }
    // afterSwap's own `elt` is the target: the element that sent the request is the config's.
    var requester = evt.detail.requestConfig ? evt.detail.requestConfig.elt : null;
    var replaced = requester instanceof Element ? requester.closest('dialog') : null;
    found.forEach(function (dialog) {
      dialog.removeAttribute('data-autoopen');
      if (!dialog.open && typeof dialog.showModal === 'function') {
        openers.set(dialog, replaced && openers.has(replaced) ? openers.get(replaced) : requester);
        dialog.addEventListener('close', function () {
          returnFocus(dialog);
        });
        dialog.showModal();
      }
    });
  });

  // A place marked data-reveal is scrolled into view when an answer is swapped into it: a server's
  // job answers at the top of the page, far from the button that asked (O9 user-walk, A13). Only
  // the place itself: its polling row swaps itself, and the page does not jump every 2 s.
  document.addEventListener('htmx:afterSwap', function (evt) {
    var target = evt.detail.target;
    if (target instanceof Element && target.matches('[data-reveal]') && typeof target.scrollIntoView === 'function') {
      target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  });

  // A form inside a dialog closes it once its request is answered, also on an error: the modal
  // would otherwise hide the notice that explains it. Any other element closes it only when a
  // write it sent fails (a problem's "Quitar", hx-delete, in the build dialog); a read leaves it
  // open, failed or not: what a dialog loads for itself (the update preview when it opens, the
  // build select) and a mod's card opened from it (the drawer), so the choices in it stay. htmx swaps before this runs: a
  // dialog the answer replaced (the build dialog, recalculated) or took away (the build was
  // created) is no longer on the page and is not closed, since its close would take the focus
  // out of the new one; taken away, the focus goes back as on a close. A sender the answer took
  // off the page gets no event here (it does not bubble from outside the document): htmx fires it
  // again on its closest parent still on the page, with that parent as `elt`, so the sender is
  // the config's.
  document.addEventListener('htmx:afterRequest', function (evt) {
    var elt = evt.detail.requestConfig ? evt.detail.requestConfig.elt : evt.detail.elt;
    var dialog = elt instanceof Element ? elt.closest('dialog') : null;
    var verb = evt.detail.requestConfig ? evt.detail.requestConfig.verb : 'get';
    var failedWrite = evt.detail.failed && verb !== 'get';
    if (!dialog || !dialog.open || !(elt instanceof HTMLFormElement || failedWrite)) {
      return;
    }
    if (!dialog.isConnected) {
      var current = dialog.id ? document.getElementById(dialog.id) : null;
      if (!current || !current.open) {
        returnFocus(dialog);
      }
      return;
    }
    dialog.close();
  });

  // The notice that undoes a row's pin (A14) goes once its "Deshacer" is answered; not while the
  // answer is the downgrade dialog (HX-Retarget #pin-dialog), since cancelling it would lose the undo.
  document.addEventListener('htmx:afterRequest', function (evt) {
    var elt = evt.detail.requestConfig ? evt.detail.requestConfig.elt : evt.detail.elt;
    var notice = elt instanceof Element ? elt.closest('[data-notice]') : null;
    var asks = evt.detail.xhr && evt.detail.xhr.getResponseHeader('HX-Retarget') === '#pin-dialog';
    if (notice && evt.detail.successful && !asks) notice.remove();
  });

  // "Fijar otra" (a problem's card) lands on the mod in the list, #mod-row-<id>: its card opens,
  // which loads its versions to pin. On load, once htmx has processed the page, so the card's
  // toggle reaches it; on hashchange when the page is already the one linked (?vista=todos), and
  // then the modal the link was clicked in closes, so the row is reachable.
  function openLinkedMod() {
    if (location.hash.indexOf('#mod-row-') !== 0) {
      return;
    }
    var row = document.getElementById(location.hash.slice(1));
    var details = row ? row.querySelector('details') : null;
    if (!details) {
      return;
    }
    var reveal = function () {
      details.open = true;
      var summary = details.querySelector('summary');
      if (summary) {
        summary.focus();
      }
      row.scrollIntoView({ block: 'start' });
    };
    var dialog = document.querySelector('dialog[open]');
    if (!dialog) {
      reveal();
      return;
    }
    // After the dialog's own close listener, which gives the focus back to its opener: the row
    // takes it from there.
    dialog.addEventListener('close', reveal, { once: true });
    dialog.close();
  }
  window.addEventListener('load', openLinkedMod);
  window.addEventListener('hashchange', openLinkedMod);

  // A form's submit buttons are busy while it is sent: no double submit (round 1 review). An htmx
  // form gets them back when its request ends; a plain form leaves the page, and its buttons are
  // disabled only after the browser has read the clicked one's value.
  function submitButtons(form) {
    return form.tagName === 'FORM' ? form.querySelectorAll('button[type=submit]:not([disabled])') : [];
  }
  document.addEventListener('htmx:beforeRequest', function (evt) {
    submitButtons(evt.detail.elt).forEach(function (button) {
      button.disabled = true;
      button.setAttribute('data-busy', '');
    });
  });
  document.addEventListener('htmx:afterRequest', function (evt) {
    var form = evt.detail.elt;
    if (form.tagName !== 'FORM') {
      return;
    }
    form.querySelectorAll('button[data-busy]').forEach(function (button) {
      button.disabled = false;
      button.removeAttribute('data-busy');
    });
  });
  document.addEventListener('submit', function (evt) {
    var form = evt.target;
    if (!(form instanceof HTMLFormElement) || form.hasAttribute('hx-post') || form.hasAttribute('hx-put')) {
      return;
    }
    setTimeout(function () {
      submitButtons(form).forEach(function (button) {
        button.disabled = true;
        button.setAttribute('data-busy', '');
      });
    }, 0);
  });

  // An upload says how far it is (O9 user-walk, A11): a Prism zip of up to 1 GiB takes minutes. Its
  // button reads its `data-progress` text and the share sent; htmx gives it back when it ends.
  document.addEventListener('htmx:xhr:progress', function (evt) {
    var form = evt.detail.elt;
    var button = form instanceof Element ? form.querySelector('button[data-progress]') : null;
    if (!button || !evt.detail.lengthComputable || !evt.detail.total) {
      return;
    }
    if (!button.hasAttribute('data-progress-from')) {
      button.setAttribute('data-progress-from', button.innerHTML);
    }
    button.textContent = button.getAttribute('data-progress') + ' ' +
      Math.floor(100 * evt.detail.loaded / evt.detail.total) + ' %';
    button.setAttribute('aria-busy', 'true');
  });
  document.addEventListener('htmx:afterRequest', function (evt) {
    var form = evt.detail.elt;
    var button = form instanceof Element ? form.querySelector('button[data-progress-from]') : null;
    if (button) {
      button.innerHTML = button.getAttribute('data-progress-from');
      button.removeAttribute('data-progress-from');
      button.removeAttribute('aria-busy');
    }
  });

  // A note's draft (O8 tanda 2, user-walk N7, N8): a form marked data-draft keeps what is typed in this
  // browser, puts it back when the same dialog opens again (a reload, an expired session), and forgets it
  // once saved. Storage may be off or full: then there is simply no draft.
  function draftKey(form) {
    return 'draft:' + form.getAttribute('data-draft');
  }
  document.addEventListener('input', function (evt) {
    var form = evt.target instanceof Element ? evt.target.closest('form[data-draft]') : null;
    if (!form) {
      return;
    }
    var fields = {};
    form.querySelectorAll('textarea, input[type=radio]:checked, select').forEach(function (field) {
      fields[field.name] = field.value;
    });
    try {
      localStorage.setItem(draftKey(form), JSON.stringify(fields));
    } catch (e) { /* no draft */ }
  });
  document.addEventListener('htmx:afterSwap', function (evt) {
    var target = evt.detail.target;
    var form = target instanceof Element ? target.querySelector('form[data-draft]') : null;
    if (!form) {
      return;
    }
    var saved = null;
    try {
      saved = JSON.parse(localStorage.getItem(draftKey(form)) || 'null');
    } catch (e) { /* no draft */ }
    if (!saved) {
      return;
    }
    Object.keys(saved).forEach(function (name) {
      form.querySelectorAll('[name="' + name + '"]').forEach(function (field) {
        if (field.type === 'radio') {
          field.checked = field.value === saved[name];
        } else if (!field.value) {
          field.value = saved[name];
        }
      });
    });
  });
  document.addEventListener('htmx:afterRequest', function (evt) {
    var form = evt.detail.elt instanceof Element ? evt.detail.elt.closest('form[data-draft]') : null;
    if (form && evt.detail.successful) {
      try {
        localStorage.removeItem(draftKey(form));
      } catch (e) { /* no draft */ }
    }
  });

  // A plain form marked data-submit-on-change is sent when one of its fields changes (O9 D12: the
  // jobs' filters, as the mods' do through htmx).
  document.addEventListener('change', function (evt) {
    var form = evt.target instanceof Element ? evt.target.closest('form[data-submit-on-change]') : null;
    if (form && typeof form.requestSubmit === 'function') {
      form.requestSubmit();
    }
  });

  // A link or a plain form's button to a page that waits for Modrinth (a pack's updates, comparing
  // two builds: up to ~15 s) says it is loading once clicked (O6): its `data-loading` text, busy.
  function showLoading(element) {
    if (!element.hasAttribute('data-loading-from')) {
      element.setAttribute('data-loading-from', element.innerHTML);
    }
    element.textContent = element.getAttribute('data-loading');
    element.setAttribute('aria-busy', 'true');
  }
  document.addEventListener('click', function (evt) {
    // A new tab or window (a modifier, another button) leaves this page as it is.
    if (evt.defaultPrevented || evt.button !== 0 || evt.metaKey || evt.ctrlKey || evt.shiftKey || evt.altKey) {
      return;
    }
    var link = evt.target instanceof Element ? evt.target.closest('a[data-loading]') : null;
    if (link && !link.hasAttribute('target')) {
      showLoading(link);
    }
  });
  document.addEventListener('submit', function (evt) {
    var form = evt.target;
    if (evt.defaultPrevented || !(form instanceof HTMLFormElement) || form.hasAttribute('hx-post') ||
        form.hasAttribute('hx-get') || form.hasAttribute('hx-put')) {
      return;
    }
    var button = evt.submitter && evt.submitter.matches('button[data-loading]') ? evt.submitter
      : form.querySelector('button[data-loading]');
    if (button) {
      showLoading(button);
    }
  });
  // Back to this page from the browser's cache: what was loading is shown as it was.
  window.addEventListener('pageshow', function (evt) {
    if (!evt.persisted) {
      return;
    }
    document.querySelectorAll('[data-loading-from]').forEach(function (element) {
      element.innerHTML = element.getAttribute('data-loading-from');
      element.removeAttribute('data-loading-from');
      element.removeAttribute('aria-busy');
    });
    document.querySelectorAll('button[data-busy]').forEach(function (button) {
      button.disabled = false;
      button.removeAttribute('data-busy');
    });
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
