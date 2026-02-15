"use strict";

const config = window.BID_RSS_LP_CONFIG || {};

function renderCheckoutState() {
  const checkoutLink = document.getElementById("checkout-link");
  const checkoutNote = document.getElementById("checkout-note");
  const planName = document.getElementById("plan-name");
  const supportMail = document.getElementById("support-mail");

  if (planName && config.planName) {
    planName.textContent = config.planName;
  }

  const supportAddress = config.supportEmail || "support@example.com";
  if (supportMail) {
    supportMail.textContent = supportAddress;
    supportMail.href = `mailto:${supportAddress}`;
  }

  if (!checkoutLink || !checkoutNote) {
    return;
  }

  if (!config.checkoutUrl) {
    checkoutLink.classList.add("disabled");
    checkoutLink.href = "#";
    checkoutNote.textContent =
      "現在、有料プラン導線は準備中です。管理者は LP_CHECKOUT_URL を設定してください。";
    return;
  }

  checkoutLink.classList.remove("disabled");
  checkoutLink.href = config.checkoutUrl;
  checkoutNote.textContent = "";
}

function safeText(value) {
  return String(value || "").replace(/[&<>\"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case "\"":
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return char;
    }
  });
}

function renderFreeList(items) {
  const freeList = document.getElementById("free-list");
  if (!freeList) {
    return;
  }
  if (!Array.isArray(items) || items.length === 0) {
    freeList.innerHTML = "<li>無料枠データは準備中です。</li>";
    return;
  }

  freeList.innerHTML = items
    .map((item) => {
      const title = safeText(item.title);
      const url = safeText(item.url);
      const organization = safeText(item.organization || "-");
      const date = safeText(item.date || "-");
      return (
        `<li><a href="${url}" target="_blank" rel="noopener">${title}</a>` +
        `<span class="meta">${organization} | ${date}</span></li>`
      );
    })
    .join("");
}

async function loadFreeData() {
  try {
    const response = await fetch("./free_today.json", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`status=${response.status}`);
    }
    const payload = await response.json();
    renderFreeList(payload.items || []);
  } catch (error) {
    renderFreeList([]);
    const freeList = document.getElementById("free-list");
    if (freeList) {
      freeList.insertAdjacentHTML(
        "beforeend",
        `<li class="meta">データ取得エラー: ${safeText(error.message)}</li>`
      );
    }
  }
}

renderCheckoutState();
loadFreeData();
