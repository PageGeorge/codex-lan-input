const textInput = document.getElementById("text-input");
const sendButton = document.getElementById("send-button");
const enterButton = document.getElementById("enter-button");
const statusElement = document.getElementById("status");

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
  enterButton.disabled = isBusy;
}

function setStatus(message, tone) {
  statusElement.textContent = message;
  statusElement.className = `status status-${tone}`;
}

async function postJson(url, payload) {
  let response;

  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    throw new Error("无法连接到电脑上的服务");
  }

  let data = { ok: false, message: "Unexpected response" };

  try {
    data = await response.json();
  } catch (error) {
    throw new Error("Server returned invalid JSON");
  }

  if (!response.ok || !data.ok) {
    throw new Error(data.message || "Request failed");
  }

  return data;
}

sendButton.addEventListener("click", async () => {
  if (!textInput.value.trim()) {
    setStatus("请输入要发送的文本", "error");
    textInput.focus();
    return;
  }

  setBusy(true);
  setStatus("正在发送文本…", "pending");

  try {
    const data = await postJson("/api/send", { text: textInput.value });
    textInput.value = "";
    setStatus(data.message, "success");
    textInput.focus();
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
  }
});

enterButton.addEventListener("click", async () => {
  setBusy(true);
  setStatus("正在发送回车…", "pending");

  try {
    const data = await postJson("/api/enter", {});
    setStatus(data.message, "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    setBusy(false);
  }
});
