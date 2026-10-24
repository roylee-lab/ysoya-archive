// 지우기 같은 되돌리기 어려운 동작은 한 번 더 묻기
document.addEventListener("submit", function (e) {
  var msg = e.target.getAttribute("data-confirm");
  if (msg && !window.confirm(msg)) e.preventDefault();
});
