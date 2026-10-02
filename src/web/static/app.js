// The panel's own script: what htmx does not cover. Loaded from /static, never inline (CSP), and
// it never turns a string into code (htmx runs with allowEval: false, and so does this).
(function () {
  'use strict';

  // Errors a route answers with a notice fragment (_notice.html): shown in #notices.
  var NOTICE_STATUSES = [403, 404, 409, 422];

  function notices() {
    return document.getElementById('notices');
  }

  // Put a copy of a <template> from base.html in #notices; `extra` is appended as text.
  function showTemplate(id, extra) {
    var target = notices();
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

  document.addEventListener('htmx:beforeSwap', function (evt) {
    var status = evt.detail.xhr.status;
    if (NOTICE_STATUSES.indexOf(status) !== -1 && notices()) {
      evt.detail.shouldSwap = true;
      evt.detail.target = notices();
      // The notice replaces the previous one, whatever the triggering element asked for.
      evt.detail.swapOverride = 'innerHTML';
      evt.detail.selectOverride = 'unset';
    } else if (status >= 500) {
      evt.detail.shouldSwap = false;
      showTemplate('notice-server-error', evt.detail.xhr.getResponseHeader('X-Request-ID') || 'sin identificador');
    }
  });

  document.addEventListener('htmx:sendError', function () {
    showTemplate('notice-network-error');
  });

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

  // A form inside a confirmation dialog closes it once its request is answered, also on an
  // error: the modal would otherwise hide the notice that explains it.
  document.addEventListener('htmx:afterRequest', function (evt) {
    var dialog = evt.detail.elt instanceof Element ? evt.detail.elt.closest('dialog') : null;
    if (dialog && dialog.open) {
      dialog.close();
    }
  });
})();
