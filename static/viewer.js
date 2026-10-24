// 보기 화면: 캡처 시점 고르기, ← → 키로 이전·다음 캡처
(function () {
  var sel = document.getElementById("ver");
  if (sel) sel.addEventListener("change", function () { if (sel.value) location.href = sel.value; });
  document.addEventListener("keydown", function (e) {
    if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
    var id = e.key === "ArrowLeft" ? "prev" : e.key === "ArrowRight" ? "next" : null;
    if (!id) return;
    var a = document.getElementById(id);
    if (a && a.getAttribute("aria-disabled") !== "true") location.href = a.href;
  });
})();
