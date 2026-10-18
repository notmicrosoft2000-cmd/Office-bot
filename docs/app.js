/* Office Bot — tiny progressive-enhancement script (tabs + footer year). */
(function () {
  "use strict";

  // ARIA tabs: click and arrow-key navigation.
  document.querySelectorAll('[role="tablist"]').forEach(function (list) {
    var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
    if (!tabs.length) return;

    function select(tab) {
      tabs.forEach(function (t) {
        var panel = document.getElementById(t.getAttribute("aria-controls"));
        var on = t === tab;
        t.setAttribute("aria-selected", on ? "true" : "false");
        if (panel) panel.hidden = !on;
      });
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { select(tab); });
      tab.addEventListener("keydown", function (e) {
        var dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!dir) return;
        e.preventDefault();
        var next = tabs[(i + dir + tabs.length) % tabs.length];
        next.focus();
        select(next);
      });
    });

    select(tabs.filter(function (t) {
      return t.getAttribute("aria-selected") === "true";
    })[0] || tabs[0]);
  });

  // Footer/year placeholders.
  var year = String(new Date().getFullYear());
  document.querySelectorAll(".js-year").forEach(function (el) {
    el.textContent = year;
  });
})();
