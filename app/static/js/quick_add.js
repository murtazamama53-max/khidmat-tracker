let qaLastPreview = null;
let qaDebounceTimer = null;

function openQuickAdd() {
  document.getElementById("quickAddBackdrop").classList.add("open");
  document.getElementById("qa-text").focus();
}

function closeQuickAdd() {
  document.getElementById("quickAddBackdrop").classList.remove("open");
  document.getElementById("qa-text").value = "";
  document.getElementById("qa-preview").innerHTML = "";
  document.getElementById("qa-error").style.display = "none";
  document.getElementById("qa-confirm-btn").disabled = true;
  qaLastPreview = null;
}

function fmtMoney(v) {
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

async function runPreview() {
  const text = document.getElementById("qa-text").value.trim();
  const date = document.getElementById("qa-date").value;
  const previewEl = document.getElementById("qa-preview");
  const errorEl = document.getElementById("qa-error");
  const confirmBtn = document.getElementById("qa-confirm-btn");

  errorEl.style.display = "none";
  confirmBtn.disabled = true;
  qaLastPreview = null;

  if (!text) {
    previewEl.innerHTML = "";
    return;
  }

  try {
    const res = await fetch("/sessions/parse-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify({ text, date }),
    });
    const data = await res.json();

    if (!res.ok) {
      previewEl.innerHTML = "";
      errorEl.textContent = data.error || "Could not parse input.";
      errorEl.style.display = "block";
      return;
    }

    qaLastPreview = { date: data.date, raw_text: text, items: data.items };

    previewEl.innerHTML = data.items
      .map((item) => {
        if (item.needs_confirmation) {
          return `<div class="chip-preview needs-confirm">
            <span>${item.raw_text}</span>
            <span style="color:var(--accent-gold); font-size:12px;">${item.confirmation_reason || "Needs confirmation"}</span>
          </div>`;
        }
        return `<div class="chip-preview">
          <span><strong>${item.source_name}</strong> · ${item.duration_human}</span>
          <span class="amount-positive">${fmtMoney(item.amount)}</span>
        </div>`;
      })
      .join("");

    if (data.all_confirmed) {
      previewEl.innerHTML += `<div class="chip-total">
        <span>Total</span><span>${fmtMoney(data.total)}</span>
      </div>`;
      confirmBtn.disabled = false;
    } else {
      previewEl.innerHTML += `<p style="color:var(--text-muted); font-size:12.5px; margin-top:8px;">
        Resolve the items above (add the missing source/rate under Rates) before saving.
      </p>`;
    }
  } catch (e) {
    errorEl.textContent = "Something went wrong parsing that input.";
    errorEl.style.display = "block";
  }
}

async function confirmQuickAdd() {
  if (!qaLastPreview) return;
  const confirmBtn = document.getElementById("qa-confirm-btn");
  confirmBtn.disabled = true;
  confirmBtn.textContent = "Saving...";

  try {
    const res = await fetch("/sessions/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify(qaLastPreview),
    });
    const data = await res.json();

    if (!res.ok) {
      document.getElementById("qa-error").textContent = data.error || "Could not save.";
      document.getElementById("qa-error").style.display = "block";
      confirmBtn.disabled = false;
      confirmBtn.textContent = "Confirm & Save";
      return;
    }

    window.location.reload();
  } catch (e) {
    confirmBtn.disabled = false;
    confirmBtn.textContent = "Confirm & Save";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const textInput = document.getElementById("qa-text");
  const dateInput = document.getElementById("qa-date");
  if (!textInput) return;

  textInput.addEventListener("input", () => {
    clearTimeout(qaDebounceTimer);
    qaDebounceTimer = setTimeout(runPreview, 350);
  });
  dateInput.addEventListener("change", runPreview);
});
