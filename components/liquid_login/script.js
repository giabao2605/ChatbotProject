function getStreamlit() {
  return window.Streamlit || null;
}

function setFrameHeight() {
  const Streamlit = getStreamlit();
  if (Streamlit) {
    Streamlit.setFrameHeight(Math.max(window.innerHeight || 0, screen.height || 0, 760));
  }
}

function showError(message) {
  const el = document.getElementById("errorMessage");
  if (!el) return;
  if (message) {
    el.textContent = message;
    el.classList.add("show");
  } else {
    el.textContent = "";
    el.classList.remove("show");
  }
}

// Drag vanilla JS thay cho GSAP CDN: kéo được, thả ra đàn hồi về giữa.
function initDraggable() {
  const box = document.querySelector(".liquid-glass");
  const handle = document.querySelector(".glass-drag-handle");
  if (!box || !handle) return;

  let dragging = false;
  let startX = 0;
  let startY = 0;
  let currentX = 0;
  let currentY = 0;

  function setTransform(x, y, animate = false) {
    box.style.transition = animate ? "transform 900ms cubic-bezier(.18,1.35,.32,1)" : "none";
    box.style.transform = `translate(${x}px, ${y}px)`;
  }

  handle.addEventListener("pointerdown", (e) => {
    dragging = true;
    handle.setPointerCapture(e.pointerId);
    startX = e.clientX - currentX;
    startY = e.clientY - currentY;
    handle.style.cursor = "grabbing";
  });

  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    currentX = e.clientX - startX;
    currentY = e.clientY - startY;
    setTransform(currentX, currentY, false);
  });

  function release(e) {
    if (!dragging) return;
    dragging = false;
    currentX = 0;
    currentY = 0;
    handle.style.cursor = "grab";
    setTransform(0, 0, true);
  }

  handle.addEventListener("pointerup", release);
  handle.addEventListener("pointercancel", release);
}



function initPasswordToggle() {
  const passwordInput = document.getElementById("password");
  const toggleBtn = document.getElementById("togglePassword");
  const toggleText = document.getElementById("passwordToggleText");
  if (!passwordInput || !toggleBtn || !toggleText) return;

  toggleBtn.addEventListener("click", () => {
    const isHidden = passwordInput.type === "password";
    passwordInput.type = isHidden ? "text" : "password";
    toggleText.textContent = isHidden ? "Ẩn" : "Hiện";
    toggleBtn.setAttribute("aria-label", isHidden ? "Ẩn mật khẩu" : "Hiện mật khẩu");
    toggleBtn.setAttribute("title", isHidden ? "Ẩn mật khẩu" : "Hiện mật khẩu");
    passwordInput.focus();
  });
}

window.addEventListener("DOMContentLoaded", () => {
  const Streamlit = getStreamlit();
  if (Streamlit) {
    Streamlit.setComponentReady();
    setFrameHeight();
  }

  initDraggable();
  initPasswordToggle();

  const form = document.querySelector(".login-form");
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      showError("");
      const username = document.getElementById("username")?.value || "";
      const password = document.getElementById("password")?.value || "";
      if (Streamlit) {
        Streamlit.setComponentValue({
          username,
          password,
          submittedAt: Date.now()
        });
      }
    });
  }
});

window.addEventListener("resize", setFrameHeight);

window.addEventListener("message", (event) => {
  const data = event.data;
  if (data && data.type === "streamlit:render") {
    const args = data.args || {};
    showError(args.error || "");
    setFrameHeight();
  }
});
