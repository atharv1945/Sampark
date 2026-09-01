/* Overview page — presentation only.
 *
 * This page renders NO system state. It has no fetch, no EventSource and no
 * audit store: every number on it is a static, committed figure quoted from a
 * `results/*.json` file and labelled as such on screen. That is deliberate —
 * the trace-integrity rule says the audit log is the only source of decision
 * data, so a marketing page must not appear to be showing live decisions.
 *
 * Everything below is scroll reveal and a count-up on figures that are already
 * present in the HTML, so the page reads correctly with JavaScript disabled.
 */

'use strict';

/* ---------- scroll reveal ---------- */

const revealable = document.querySelectorAll('.band > .shell > *, .hero-copy, .hero-figure');
revealable.forEach(el => el.classList.add('reveal'));

if ('IntersectionObserver' in window) {
  const io = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('in');
        io.unobserve(entry.target);
      }
    });
  }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
  revealable.forEach(el => io.observe(el));
} else {
  revealable.forEach(el => el.classList.add('in'));
}

/* ---------- count-up on the evidence figures ----------
 * The final value is already in the DOM; this only animates towards it, and
 * restores the exact original text at the end so nothing is ever left showing
 * a rounded stand-in. */

function countUp(node) {
  const target = parseFloat(node.dataset.count);
  if (Number.isNaN(target)) return;
  const finalText = node.textContent;
  const dp = node.dataset.dp === undefined ? 1 : parseInt(node.dataset.dp, 10);
  const suffix = node.dataset.suffix || '';
  const duration = 900;
  const start = performance.now();

  function frame(now) {
    const t = Math.min((now - start) / duration, 1);
    const eased = 1 - Math.pow(1 - t, 3);
    if (t < 1) {
      const value = target * eased;
      node.textContent = (value < 0 ? '−' : '') + Math.abs(value).toFixed(dp) + suffix;
      requestAnimationFrame(frame);
    } else {
      node.textContent = finalText;   // exact committed value, restored
    }
  }
  requestAnimationFrame(frame);
}

const counters = document.querySelectorAll('[data-count]');
if ('IntersectionObserver' in window && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  const io2 = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        countUp(entry.target);
        io2.unobserve(entry.target);
      }
    });
  }, { threshold: 0.6 });
  counters.forEach(el => io2.observe(el));
}
