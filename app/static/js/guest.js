function fmtMoney(v) {
  return Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

async function guestCalculate() {
  const text = document.getElementById("g-text").value.trim();
  const rate = document.getElementById("g-rate").value;
  const errorEl = document.getElementById("g-error");
  const previewEl = document.getElementById("g-preview");

  errorEl.style.display = "none";
  previewEl.innerHTML = "";

  if (!text || !rate) {
    errorEl.textContent = "Enter both a session and an hourly rate.";
    errorEl.style.display = "block";
    return;
  }

  try {
    const res = await fetch("/guest/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      body: JSON.stringify({ text, rate }),
    });
    const data = await res.json();

    if (!res.ok) {
      errorEl.textContent = data.error || "Could not calculate.";
      errorEl.style.display = "block";
      return;
    }

    previewEl.innerHTML = data.components
      .map(
        (c) => `<div class="chip-preview">
          <span><strong>${c.source}</strong> · ${c.duration}</span>
          <span class="amount-positive">${fmtMoney(c.amount)}</span>
        </div>`
      )
      .join("") + `<div class="chip-total"><span>Total</span><span>${fmtMoney(data.total)}</span></div>`;
  } catch (e) {
    errorEl.textContent = "Something went wrong.";
    errorEl.style.display = "block";
  }
}
