const ASSISTANT_SUGGESTIONS = [
  "How much did I earn this month?",
  "How many hours did I work last month?",
  "How much is pending from tuition?",
  "Why is this month lower than last month?",
];

function assistantCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

function assistantScrollToBottom() {
  const log = document.getElementById("assistant-log");
  if (log) log.scrollTop = log.scrollHeight;
}

function assistantAppendBubble(text, who) {
  const log = document.getElementById("assistant-log");
  if (!log) return;
  const bubble = document.createElement("div");
  bubble.className = `assistant-bubble assistant-bubble-${who}`;
  bubble.textContent = text;
  log.appendChild(bubble);
  assistantScrollToBottom();
}

function assistantRenderSuggestions() {
  const wrap = document.getElementById("assistant-suggestions");
  if (!wrap) return;
  wrap.innerHTML = ASSISTANT_SUGGESTIONS.map(
    (q) => `<button type="button" class="assistant-chip" onclick="assistantAskSuggestion(this)">${q}</button>`
  ).join("");
}

function assistantAskSuggestion(btn) {
  const input = document.getElementById("assistant-input");
  input.value = btn.textContent;
  sendAssistantMessage();
}

async function sendAssistantMessage() {
  const input = document.getElementById("assistant-input");
  const sendBtn = document.getElementById("assistant-send-btn");
  const question = input.value.trim();
  if (!question) return;

  assistantAppendBubble(question, "user");
  input.value = "";
  input.disabled = true;
  sendBtn.disabled = true;
  const originalLabel = sendBtn.textContent;
  sendBtn.textContent = "Thinking...";

  try {
    const res = await fetch("/assistant/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": assistantCsrfToken() },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    assistantAppendBubble(data.answer || data.error || "Something went wrong.", "bot");
  } catch (e) {
    assistantAppendBubble("Something went wrong reaching the assistant.", "bot");
  } finally {
    input.disabled = false;
    sendBtn.disabled = false;
    sendBtn.textContent = originalLabel;
    input.focus();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("assistant-input");
  if (!input) return;

  assistantRenderSuggestions();

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      sendAssistantMessage();
    }
  });
});
