let session_count = 1;
let max_session_count = 10;
let leftInfoPanelIsClosed = false;
const sessionProgressFlags = new Array(10).fill(0);


const XMLHttpRequestHeaders = { 'Content-Type': 'application/x-www-form-urlencoded', 'X-Requested-With': 'XMLHttpRequest' };

// Blueprint prefix injected by chat.html via window.DEPOSIT_BASE.
// Falls back to "" so the file still works in standalone mode.
const BASE = (typeof window !== "undefined" && window.DEPOSIT_BASE) ? window.DEPOSIT_BASE : "";
function urlOf(path) { return BASE + path; }

/**
 * Extract text from a fetch Response, but reject non-2xx replies.
 * Use this instead of `.then(r => r.text())` anywhere a partial is about
 * to be injected into #main-view — otherwise a 500 HTML error page gets
 * dumped into the DOM, obliterating the CSS and leaving a "plain HTML"
 * view that the user cannot recover from without a hard refresh.
 */
function safeText(response) {
	if (!response.ok) {
		console.error("Backend error:", response.status, response.statusText, response.url);
		return null;
	}
	return response.text();
}


function refreshClickHandlers() {
	const sessionNewButton = document.getElementById("session-new");
	if (sessionNewButton !== null && sessionNewButton.dataset.bound !== "1") {
		sessionNewButton.dataset.bound = "1";
		sessionNewButton.addEventListener("click", funcSessionAddClick, false);
	}

	const depositReturnsButton = document.getElementById("session-deposit-returns");
	if (depositReturnsButton !== null && depositReturnsButton.dataset.bound !== "1") {
		depositReturnsButton.dataset.bound = "1";
		depositReturnsButton.addEventListener("click", funcDepositReturnsClick, false);
	}

	const sessionSwitchButtons = document.getElementsByClassName("session-switch");
	for (let i = 0; i < sessionSwitchButtons.length; i++) {
		if (sessionSwitchButtons[i].dataset.bound !== "1") {
			sessionSwitchButtons[i].dataset.bound = "1";
			sessionSwitchButtons[i].addEventListener("click", funcSessionSwitchClick, false);
		}
	}

	const choiceEntries = document.getElementsByClassName("entry choice");
	for (let i = 0; i < choiceEntries.length; i++) {
		if (choiceEntries[i].dataset.bound !== "1") {
			choiceEntries[i].dataset.bound = "1";
			choiceEntries[i].addEventListener("click", funcChoiceEntryClick, false);
		}
	}

	const pricingForm = document.getElementById("pricing-data");
	if (pricingForm && pricingForm.dataset.bound !== "1") {
		pricingForm.dataset.bound = "1";
		pricingForm.addEventListener("submit", funcPricingAskConfirmationClick, false);
	}

	const pricingConfirmFormYes = document.getElementById("pricing-confirm-yes");
	if (pricingConfirmFormYes && pricingConfirmFormYes.dataset.bound !== "1") {
		pricingConfirmFormYes.dataset.bound = "1";
		pricingConfirmFormYes.addEventListener("click", funcPricingConfirmClick, false);
	}

	const pricingConfirmFormNo = document.getElementById("pricing-confirm-no");
	if (pricingConfirmFormNo && pricingConfirmFormNo.dataset.bound !== "1") {
		pricingConfirmFormNo.dataset.bound = "1";
		pricingConfirmFormNo.addEventListener("click", funcPricingCancelConfirmationClick, false);
	}

	const leftInfoPanelToggle = document.getElementById("info-panel-left-toggle");
	if (leftInfoPanelToggle && leftInfoPanelToggle.dataset.bound !== "1") {
		leftInfoPanelToggle.dataset.bound = "1";
		leftInfoPanelToggle.addEventListener("click", funcLeftInfoPanelToggle, false);
	}

	const leftInfoPanelToggleCollapsed = document.getElementById("info-panel-left-toggle-collapsed");
	if (leftInfoPanelToggleCollapsed && leftInfoPanelToggleCollapsed.dataset.bound !== "1") {
		leftInfoPanelToggleCollapsed.dataset.bound = "1";
		leftInfoPanelToggleCollapsed.addEventListener("click", funcLeftInfoPanelToggle, false);
	}

	// Re-bind the chat-form submit handler (DOM gets replaced on every partial
	// re-render; without this the form falls back to native submit and the
	// browser navigates to a plain-HTML fragment).
	attachChatFormHandler();

	LeftInfoPanelToggleManual(!leftInfoPanelIsClosed);
	syncChatInputLock();
}

function syncChatInputLock() {
	// Hide the chat input when the active session has been confirmed
	// (confirmation_state == 2). Backend sets data-confirmed on a marker
	// element rendered inside #main-view.
	const marker  = document.getElementById("conf-state-marker");
	const inputBox = document.getElementById("chat-input-box");
	if (!inputBox) return;
	const confirmed = marker && marker.dataset.confirmed === "1";
	if (confirmed) {
		inputBox.classList.add("hidden");
	} else {
		inputBox.classList.remove("hidden");
	}
}

const funcPricingAskConfirmationClick = function(e) {
	console.log("funcPricingAskConfirmationClick triggered.");
	e.preventDefault();
	let session_index = getSelectedSession();
	
	fetch(urlOf("/pricing-session-ask-confirmation"), {
		method: "POST",
		headers: XMLHttpRequestHeaders,
		body: new URLSearchParams({
			'session_index': session_index,
		}).toString()
	})
	.then(safeText)
	.then(html => {
		if (!html) return;
		document.getElementById("main-view").innerHTML = html;
		refreshClickHandlers();
		selectSession(session_index);
	});	
}

const funcPricingCancelConfirmationClick = function(e) {
	console.log("funcPricingCancelConfirmationClick triggered.");
	e.preventDefault();
	let session_index = getSelectedSession();
	
	fetch(urlOf("/pricing-session-cancel-confirmation"), {
		method: "POST",
		headers: XMLHttpRequestHeaders,
		body: new URLSearchParams({
			'session_index': session_index,
		}).toString()
	})
	.then(safeText)
	.then(html => {
		if (!html) return;
		document.getElementById("main-view").innerHTML = html;
		refreshClickHandlers();
		selectSession(session_index);
	});	
}

const funcPricingConfirmClick = function(e) {
	console.log("funcPricingConfirmClick triggered.");
	e.preventDefault();
	let session_index = getSelectedSession();
	
	fetch(urlOf("/pricing-session-confirm"), {
		method: "POST",
		headers: XMLHttpRequestHeaders,
		body: new URLSearchParams({
			'session_index': session_index,
		}).toString()
	})
	.then(safeText)
	.then(html => {
		if (!html) return;
		document.getElementById("main-view").innerHTML = html;
		refreshClickHandlers();
		selectSession(session_index);
	});	
}

function getSelectedSession() {
	let result = -1;
	const sessionSwitchButtons = document.getElementsByClassName("session-switch")
	for (let i = 0; i < sessionSwitchButtons.length; i++) {
		if (sessionSwitchButtons[i].classList.contains("selected")) {
			result = i;
		}
	}
	console.log("getSelectedSession returned: " + result);
	return result;
}

function selectSession(index) {
	console.log("selectSession called with index: " + index);
	let idx = index
	if (idx > max_session_count - 1)
		idx = max_session_count - 1;
	const sessionSwitchButtons = document.getElementsByClassName("session-switch")
	for (let i = 0; i < sessionSwitchButtons.length; i++) {
		if (i == idx) {
			sessionSwitchButtons[i].classList.add("selected");
			// active_session_index = i;
		}
		else { 
			sessionSwitchButtons[i].classList.remove("selected");
		}
	}
	if(sessionProgressFlags[idx] == 1){          //session switch in progress 
            inProgressActions();
    }
    else
    {
        const chatSendBtn = document.getElementById("chatSendBtn");
        chatSendBtn.disabled = false; 
    }
}

function confirmSession(idx) {
	const sessionSwitchButtons = document.getElementsByClassName("session-switch")
	for (let i = 0; i < sessionSwitchButtons.length; i++) {
		if (i == idx)
			sessionSwitchButtons[i].classList.add("confirmed");
	}
}

const funcDepositReturnsClick = function(e) {
	console.log("functDepositReturnsClick triggered.");
	const selected_session_id = getSelectedSession();
	
	fetch(urlOf("/return-list"), {
		method: "POST",
		headers: XMLHttpRequestHeaders
	})
	.then(safeText)
	.then(html => {
		if (!html) return;
		document.getElementById("main-view").innerHTML = html;
				
		refreshClickHandlers();
		selectSession(selected_session_id);
	});
}


async function funcChoiceEntryClick(e) {
	// Use closest() to find the data-bearing root, not the nearest div
	// (Tabler card wraps create intermediate .card / .card-body divs).
	const target_div = e.target.closest(".entry.choice");
	if (!target_div) {
		console.warn("funcChoiceEntryClick: no .entry.choice ancestor found");
		return;
	}
	const cust_id       = target_div.dataset.custId;
	const amount        = target_div.dataset.amount;
	const currency      = target_div.dataset.currency;
	const tenor         = target_div.dataset.tenor;
	const return_index  = target_div.dataset.returnIndex;

	// Guard: if any of the data-* attrs is missing/undefined, abort cleanly
	// instead of POSTing garbage that would 500 the backend.
	if (!cust_id || !amount || !currency || !tenor || return_index === undefined || return_index === "undefined") {
		console.warn("funcChoiceEntryClick: missing data attrs", {cust_id, amount, currency, tenor, return_index});
		return;
	}

	let session_index = getEmptyPricingSession();
	if (session_index === -1) {
		session_index = await funcSessionAddClick(e) - 1;
	} else {
		await switchSession(session_index);
	}

	// Bind the return to the session
	try {
		const r = await fetch(urlOf("/return-session-set"), {
			method: "POST",
			headers: XMLHttpRequestHeaders,
			body: new URLSearchParams({
				'return_index': return_index,
				'session_index': session_index
			}).toString()
		});
		if (!r.ok) {
			console.error("return-session-set failed:", r.status);
			return;
		}
	} catch (err) {
		console.error("return-session-set threw:", err);
		return;
	}

	// Compose the message and submit the chat form the same way a human would.
	// We do NOT click the send button — that triggers a form submit event
	// whose handler may or may not be attached yet after the main-view was
	// just replaced by switchSession. Instead call handleChatFormSubmit
	// directly with a synthetic event.
	const userInputField = document.getElementById("user_input");
	if (!userInputField) {
		console.warn("funcChoiceEntryClick: user_input field missing");
		return;
	}
	const msg = "Merhaba, " + cust_id + " müşteri no için " + tenor + " günde " +
	            amount + " " + currency + " tutarında mevduat fiyatı rica ediyorum.";
	userInputField.value = msg;

	const conversationDiv = document.getElementById("conversation");
	if (conversationDiv) conversationDiv.innerHTML = "";

	// Fire the submit programmatically; handleChatFormSubmit reads the input
	// field value, not e.submitter.value.
	const chatForm = document.getElementById("chat-input");
	if (chatForm && typeof chatForm.requestSubmit === "function") {
		chatForm.requestSubmit();
	} else {
		// Fallback: synthesize a submit event
		const ev = new Event("submit", {cancelable: true, bubbles: true});
		chatForm && chatForm.dispatchEvent(ev);
	}
}

async function funcSessionAddClick(e) {
	console.log("funcSessionAddClick triggered.");
	const selected_session_id = getSelectedSession();
	
	return fetch(urlOf("/pricing-session-add"), {
		method: "POST",
		headers: XMLHttpRequestHeaders
	})
	.then(safeText)
	.then(html => {
		if (!html) return;
		document.getElementById("info-panel-left").innerHTML = html;
		session_count = document.getElementsByClassName("session-switch").length;
		console.log("session_count after adding: " + session_count);
		if (session_count > max_session_count) {
			session_count = max_session_count;
		}
		
		console.log("funcSessionAddClick: session_count = " + session_count + ", session_index = " + (session_count - 1).
		toString());
		
		refreshClickHandlers();
		switchSession(session_count - 1);
		return session_count;
	});
}

async function switchSession(session_index) {
	console.log("switchSession triggered with session_index: " + session_index);

	const mainDiv = document.getElementById("main-view");
	return fetch(urlOf("/pricing-session-switch"), {
		method: "POST",
		headers: XMLHttpRequestHeaders,
		body: new URLSearchParams({
			'session_index': session_index,
		}).toString()
	})
	.then(safeText)
	.then(html => {
		if (!html) return;
		mainDiv.innerHTML = html	
		refreshClickHandlers();
		selectSession(session_index);
	});

}

const funcSessionSwitchClick = function(e) {
	const mainDiv = document.getElementById("main-view");
	
	const session_index = e.srcElement.value
	console.log("funcSessionSwitchClick triggered. Session Index: " + session_index);
	switchSession(session_index);
}

function LeftInfoPanelToggleManual(state) {
	// state=true → expand; state=false → collapse.
	// Does NOT touch toggle button innerHTML (SVG icons stay intact);
	// CSS drives which icon is visible via .closed class.
	const leftInfoPanel = document.getElementById("info-panel-left");
	if (!leftInfoPanel) return;

	if (state) {
		leftInfoPanel.classList.remove("closed");
		leftInfoPanelIsClosed = false;
	} else {
		leftInfoPanel.classList.add("closed");
		leftInfoPanelIsClosed = true;
	}
}

const funcLeftInfoPanelToggle = function(e) {
	if (e && e.preventDefault) e.preventDefault();
	const leftInfoPanel = document.getElementById("info-panel-left");
	if (!leftInfoPanel) {
		console.warn("funcLeftInfoPanelToggle: #info-panel-left not found");
		return;
	}
	const wasClosed = leftInfoPanel.classList.contains("closed");
	console.log("funcLeftInfoPanelToggle: wasClosed=" + wasClosed + " → will " + (wasClosed ? "open" : "close"));
	// If currently closed, OPEN (state=true); if currently open, CLOSE (state=false)
	LeftInfoPanelToggleManual(wasClosed);
}

function getEmptyPricingSession() {
	const sessionSwitchButtons = document.getElementsByClassName("session-switch")
	console.log("sessionSwitchButtons length: " + sessionSwitchButtons.length);
	for (let i = 0; i < sessionSwitchButtons.length; i++) {
		// Tabler buttons include an SVG icon, so we compare text content only.
		const txt = (sessionSwitchButtons[i].innerText || "").trim();
		if (txt === "Yeni Fiyat") {
			return i;
		}
	}
	return -1;
}

function findParent(startElement, tagName) {
  let currentElm = startElement;
  while (currentElm != document.body) {
    if (currentElm.tagName.toLowerCase() == tagName.toLowerCase()) { return currentElm; }
    currentElm = currentElm.parentElement;
  }
  return false;
}

function inProgressActions(){
    // Add "assistant is typing..." message
    console.log("in progress...");
    const conversationDiv = document.getElementById("conversation");
    const typingMessageDiv = document.createElement("div");
    typingMessageDiv.className = "entry-container assistant";
    typingMessageDiv.style.fontStyle = "italic";
    typingMessageDiv.innerHTML = "<div class='avatar assistant'></div><div class='entry assistant loading'>Mevduat Asistanı yazıyor...</div>";
    conversationDiv.appendChild(typingMessageDiv);
    const chatSendBtn = document.getElementById("chatSendBtn");
    chatSendBtn.disabled = true; 

}

function attachChatFormHandler() {
	const chatForm = document.getElementById("chat-input");
	if (!chatForm) return;
	// Avoid double-binding after partial re-render
	if (chatForm.dataset.handlerAttached === "1") return;
	chatForm.dataset.handlerAttached = "1";
	chatForm.addEventListener("submit", handleChatFormSubmit, false);
}

function handleChatFormSubmit(e) {
	e.preventDefault();
	const userInputField = document.getElementById("user_input");
	const mainDiv = document.getElementById("main-view");
	const actionValue = e.submitter ? e.submitter.value : "Gönder";

	if (actionValue === "Gönder") {
		const userText = userInputField.value.trim();
		if (userText === "") return;

		const conversationDiv = document.getElementById("conversation");
		const chatSendBtn = document.getElementById("chatSendBtn");
		if (chatSendBtn) chatSendBtn.disabled = true;
		let index = getSelectedSession();
		if (index >= 0) sessionProgressFlags[index] = 1;

		// Optimistic user message
		const newUserMessageDiv = document.createElement("div");
		newUserMessageDiv.className = "entry-container user";
		newUserMessageDiv.innerHTML = "<div class='avatar user'></div><div class='entry user'></div>";
		newUserMessageDiv.querySelector(".entry").textContent = userText;
		conversationDiv.appendChild(newUserMessageDiv);

		// "Assistant is typing..."
		const typingMessageDiv = document.createElement("div");
		typingMessageDiv.className = "entry-container assistant";
		typingMessageDiv.style.fontStyle = "italic";
		typingMessageDiv.innerHTML = "<div class='avatar assistant'></div><div class='entry assistant loading'>Mevduat Asistanı yazıyor...</div>";
		conversationDiv.appendChild(typingMessageDiv);

		conversationDiv.scrollTop = conversationDiv.scrollHeight;
		userInputField.value = '';

		fetch(urlOf("/"), {
			method: "POST",
			headers: {
				'Content-Type': 'application/x-www-form-urlencoded',
				'X-Requested-With': 'XMLHttpRequest'
			},
			body: new URLSearchParams({
				'user_input': userText,
				'action': 'Send'
			}).toString()
		})
		.then(response => {
			if (!response.ok) {
				console.error('Backend error:', response.status, response.statusText);
				return null;
			}
			return response.text();
		})
		.then(html => {
			if (index >= 0) sessionProgressFlags[index] = 0;
			if (typingMessageDiv.parentNode) typingMessageDiv.parentNode.removeChild(typingMessageDiv);
			if (chatSendBtn) chatSendBtn.disabled = false;
			if (html === null) return;

			if (!html) return;
			mainDiv.innerHTML = html;
			refreshClickHandlers();
			if (index >= 0) selectSession(index);

			const lastMessage = document.getElementById("anchor");
			if (lastMessage) lastMessage.scrollIntoView();
		})
		.catch(error => {
			console.error('Error:', error);
			if (typingMessageDiv.parentNode) typingMessageDiv.parentNode.removeChild(typingMessageDiv);
			if (chatSendBtn) chatSendBtn.disabled = false;
		});

	} else if (actionValue === "Sıfırla") {
		fetch(urlOf("/app-reset"), {
			method: "POST",
			headers: {
				'Content-Type': 'application/x-www-form-urlencoded',
				'X-Requested-With': 'XMLHttpRequest'
			},
			body: new URLSearchParams({ 'action': 'Reset' }).toString()
		})
		.then(response => response.ok ? response.text() : null)
		.then(html => {
			if (html === null) return;
			mainDiv.innerHTML = html;
			session_count = 1;
			leftInfoPanelIsClosed = false;
			LeftInfoPanelToggleManual(!leftInfoPanelIsClosed);
			refreshClickHandlers();
			selectSession(0);
		})
		.catch(error => console.error('Error:', error));
	}
}

document.addEventListener("DOMContentLoaded", function() {
	refreshClickHandlers();
	selectSession(0);
});

// particlesJS.load('particles-js', 'static/particles.json', function() {
//   console.log('callback - particles.js config loaded');
// });