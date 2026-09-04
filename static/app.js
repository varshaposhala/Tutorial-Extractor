const form = document.getElementById("extract-form");
const startBtn = document.getElementById("start-btn");
const logEl = document.getElementById("log");
const statusPill = document.getElementById("status-pill");
const downloads = document.getElementById("downloads");
const summariesEl = document.getElementById("summaries");
const xlsxLink = document.getElementById("xlsx-link");
const csvLink = document.getElementById("csv-link");
const taskField = document.getElementById("task-field");
const taskTitle = document.getElementById("task-title");
const taskSteps = document.getElementById("task-steps");
const taskHint = document.getElementById("task-hint");

const TASKS = {
  resource: {
    title: "Learning resource ID",
    button: "Extract from resource IDs",
    hint: "Admin only. Paste one or more learning resource UUIDs. Excel groups content by unit name when that name is known.",
    show: ["fields-resource", "fields-admin"],
    steps: [
      "Paste learning resource ID(s).",
      "Log in to NKB admin.",
      "Open each learning resource and copy content_en.",
      "Find the tutorial, list steps, skip DEFAULT_QUESTIONS.",
      "Copy each step id_content to Excel and CSV.",
    ],
  },
  course: {
    title: "Course ID",
    button: "Extract from course",
    hint: "Walks every topic. Only TUTORIAL units from units_details/v3 are kept. Files are grouped by unit name, steps in tutorial order.",
    show: ["fields-course", "fields-portal", "fields-admin"],
    steps: [
      "Paste the course ID, plus phone and OTP.",
      "Log in to learning.ccbp.in.",
      "Read topics, then wait for reload and copy units from inspect.",
      "Keep only TUTORIAL unit IDs from v3 (v4 if v3 is missing).",
      "Open each tutorial set, copy resource_id from the set request.",
      "Log in to admin and extract those resources by unit, in tutorial order.",
    ],
  },
  topic: {
    title: "Topic ID",
    button: "Extract from topic",
    hint: "Opens only the topic(s) you list. Course ID is required unless the URL already has c_id.",
    show: ["fields-topic", "fields-course", "fields-portal", "fields-admin"],
    steps: [
      "Paste topic ID(s), or a course URL with t_id.",
      "Add the course ID if the URL does not include c_id.",
      "Log in to learning.ccbp.in with phone and OTP.",
      "Wait for reload, then copy TUTORIAL units from units_details/v3.",
      "Open each tutorial set, copy resource_id from inspect.",
      "Log in to admin and extract those resources by unit, in tutorial order.",
    ],
  },
  unit: {
    title: "Unit ID",
    button: "Extract from unit IDs",
    hint: "Opens only the units you list. Course ID is required unless the URL already has c_id and t_id.",
    show: ["fields-unit", "fields-course", "fields-portal", "fields-admin"],
    steps: [
      "Paste unit ID(s), or a course URL with s_id.",
      "Add course ID (and topic ID if you already know it).",
      "Log in to learning.ccbp.in with phone and OTP.",
      "If topic ID is missing, scan topics until the unit IDs match.",
      "Open each unit set page, wait for reload, copy resource_id from inspect.",
      "Log in to admin and extract those resources by unit, in tutorial order.",
    ],
  },
};

function setTask(taskName) {
  const task = TASKS[taskName] || TASKS.resource;
  taskField.value = taskName;
  taskTitle.textContent = task.title;
  startBtn.textContent = task.button;
  taskHint.textContent = task.hint;
  taskSteps.innerHTML = "";
  for (const step of task.steps) {
    const item = document.createElement("li");
    item.textContent = step;
    taskSteps.appendChild(item);
  }
  for (const group of document.querySelectorAll(".field-group")) {
    group.hidden = !task.show.includes(group.id);
  }
  document.querySelectorAll(".task-card").forEach((card) => {
    const active = card.dataset.task === taskName;
    card.classList.toggle("is-active", active);
    card.setAttribute("aria-selected", active ? "true" : "false");
  });
}

document.querySelectorAll(".task-card").forEach((card) => {
  card.addEventListener("click", () => setTask(card.dataset.task));
});

function headersFromForm(data) {
  const headers = { "Content-Type": "application/json" };
  if (data.access_code) {
    headers["X-Access-Code"] = data.access_code;
  }
  return headers;
}

function setStatus(text, className) {
  statusPill.textContent = text;
  statusPill.className = `pill ${className || ""}`.trim();
}

function addLog(message) {
  const item = document.createElement("li");
  item.textContent = message;
  logEl.appendChild(item);
  logEl.scrollTop = logEl.scrollHeight;
}

function renderSummaries(summaries) {
  summariesEl.innerHTML = "";
  if (!summaries || !summaries.length) {
    summariesEl.hidden = true;
    return;
  }
  summariesEl.hidden = false;
  for (const item of summaries) {
    const card = document.createElement("article");
    card.className = "summary-card";
    const title = document.createElement("h3");
    title.textContent = item.title || item.unit_name || item.resource_id;
    const body = document.createElement("p");
    if (item.error) {
      body.textContent = `Failed: ${item.error}`;
    } else {
      const topic = item.topic_name ? `${item.topic_name}\n` : "";
      const unit = item.unit_name ? `Unit: ${item.unit_name}\n` : "";
      const preview = item.content_en_preview
        ? `content_en: ${item.content_en_preview}`
        : "content_en: (empty)";
      body.textContent = `${topic}${unit}${item.resource_id}\nTutorial ${item.tutorial_id || "-"} · ${item.step_count} step(s)\n${preview}`;
    }
    card.append(title, body);
    summariesEl.appendChild(card);
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollJob(jobId) {
  let seenLogs = 0;
  while (true) {
    const response = await fetch(`/api/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) {
      throw new Error(job.error || "Job not found.");
    }
    while (seenLogs < job.logs.length) {
      addLog(job.logs[seenLogs].message);
      seenLogs += 1;
    }
    if (job.status === "done") {
      setStatus("Done", "done");
      renderSummaries(job.summaries);
      xlsxLink.href = `/api/jobs/${jobId}/download/xlsx`;
      csvLink.href = `/api/jobs/${jobId}/download/csv`;
      downloads.hidden = false;
      return;
    }
    if (job.status === "error") {
      setStatus("Error", "error");
      throw new Error(job.error || "Extraction failed.");
    }
    await sleep(800);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  logEl.innerHTML = "";
  downloads.hidden = true;
  summariesEl.hidden = true;
  summariesEl.innerHTML = "";
  setStatus("Starting", "running");
  startBtn.disabled = true;

  const data = Object.fromEntries(new FormData(form).entries());
  const task = (data.task || "resource").trim();
  const otpValue = (document.getElementById("portal-otp")?.value || data.portal_otp || data.otp || "").trim();

  if ((task === "course" || task === "topic" || task === "unit") && !/^\d{6}$/.test(otpValue)) {
    setStatus("Error", "error");
    addLog("Enter the 6-digit OTP in the OTP field.");
    startBtn.disabled = false;
    return;
  }
  if (task === "resource" && !(data.resource_ids || "").trim()) {
    setStatus("Error", "error");
    addLog("Enter at least one learning resource ID.");
    startBtn.disabled = false;
    return;
  }
  if (task === "course" && !(data.course_id || "").trim()) {
    setStatus("Error", "error");
    addLog("Enter a course ID or course URL.");
    startBtn.disabled = false;
    return;
  }
  if (task === "topic" && !(data.topic_ids || "").trim()) {
    setStatus("Error", "error");
    addLog("Enter at least one topic ID.");
    startBtn.disabled = false;
    return;
  }
  if (task === "unit" && !(data.unit_ids || "").trim()) {
    setStatus("Error", "error");
    addLog("Enter at least one unit ID.");
    startBtn.disabled = false;
    return;
  }

  try {
    const response = await fetch("/api/extract", {
      method: "POST",
      headers: headersFromForm(data),
      body: JSON.stringify({
        task,
        username: data.username,
        password: data.password,
        course_id: data.course_id,
        topic_id: data.topic_id,
        topic_ids: data.topic_ids,
        phone: data.phone,
        otp: otpValue,
        portal_otp: otpValue,
        resource_ids: data.resource_ids,
        unit_ids: data.unit_ids,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Could not start extraction.");
    }
    if (payload.task === "course" && payload.course_id) {
      addLog(`Queued course ${payload.course_id}.`);
    } else if (payload.task === "topic") {
      addLog(`Queued ${payload.topic_count} topic(s).`);
    } else if (payload.task === "unit") {
      addLog(`Queued ${payload.unit_count} unit(s).`);
    } else {
      addLog(`Queued ${payload.resource_count} resource(s).`);
    }
    await pollJob(payload.job_id);
  } catch (error) {
    setStatus("Error", "error");
    addLog(error.message);
  } finally {
    startBtn.disabled = false;
  }
});

setTask("resource");
