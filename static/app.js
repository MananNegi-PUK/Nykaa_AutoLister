document.addEventListener('DOMContentLoaded', () => {
    // Initializing icons
    lucide.createIcons();

    // Tab Navigation
    const navItems = document.querySelectorAll('.nav-item');
    const tabPanes = document.querySelectorAll('.tab-pane');
    const pageTitle = document.getElementById('page-title');
    const pageDescription = document.getElementById('page-description');

    const pageMeta = {
        dashboard: { title: "Dashboard Overview", desc: "Real-time status of your Nykaa Fashion catalog pipelines." },
        upload: { title: "Upload Center", desc: "Upload and manage directories, content sheets, and category templates." },
        generator: { title: "Listing Generator", desc: "Instantly create upload-ready sheets from style color codes." },
        learning: { title: "AI Learning Center", desc: "View learned column properties and defaults." },
        validation: { title: "Validation Center", desc: "Run integrity diagnostics on active sheets." }
    };

    navItems.forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const tabId = item.getAttribute('data-tab');
            
            navItems.forEach(n => n.classList.remove('active'));
            item.classList.add('active');

            tabPanes.forEach(pane => {
                pane.classList.remove('active');
                if (pane.id === `tab-${tabId}`) {
                    pane.classList.add('active');
                }
            });

            // Update header text
            if (pageMeta[tabId]) {
                pageTitle.textContent = pageMeta[tabId].title;
                pageDescription.textContent = pageMeta[tabId].desc;
            }

            // Trigger tab specific loads
            if (tabId === 'dashboard') loadDashboard();
            if (tabId === 'upload') loadUploadHistory();
            if (tabId === 'learning') loadLearningDropdowns();
        });
    });

    // Logging terminal stream connection
    const terminal = document.getElementById('log-terminal');
    
    function appendLog(level, time, message) {
        const line = document.createElement('div');
        line.className = `log-line log-${level.toLowerCase()}`;
        line.textContent = `[${time}] [${level.toUpperCase()}] ${message}`;
        terminal.appendChild(line);
        terminal.scrollTop = terminal.scrollHeight;
    }

    const eventSource = new EventSource('/api/logs');
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.message) {
            appendLog(data.level || 'info', data.time || 'SYSTEM', data.message);
        }
    };
    eventSource.onerror = () => {
        console.warn("SSE connection lost. Reconnecting...");
    };

    // File Drag & Drop Management (Dynamic for all 4 zones)
    const fileTypes = ['item_directory', 'content_sheet', 'category_template', 'historical_listing'];
    
    fileTypes.forEach(type => {
        const dropZone = document.getElementById(`drop-zone-${type}`);
        const fileInput = document.getElementById(`input-${type}`);
        const selectedDisplay = document.getElementById(`selected-${type}`);
        const selectedName = document.getElementById(`name-${type}`);
        const clearBtn = document.getElementById(`clear-${type}`);
        const form = document.querySelector(`form[data-type="${type}"]`);
        
        if (!dropZone || !fileInput || !selectedDisplay || !selectedName || !clearBtn || !form) return;
        
        dropZone.addEventListener('click', () => fileInput.click());
        
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.style.borderColor = '#FC2779';
            dropZone.style.backgroundColor = '#FFF0F5';
        });
        
        dropZone.addEventListener('dragleave', () => {
            dropZone.style.borderColor = '#E2E8F0';
            dropZone.style.backgroundColor = '#F8FAFC';
        });
        
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.style.borderColor = '#E2E8F0';
            dropZone.style.backgroundColor = '#F8FAFC';
            if (e.dataTransfer.files.length > 0) {
                fileInput.files = e.dataTransfer.files;
                handleSelectedFile(e.dataTransfer.files[0]);
            }
        });
        
        fileInput.addEventListener('change', () => {
            if (fileInput.files.length > 0) {
                handleSelectedFile(fileInput.files[0]);
            }
        });
        
        function handleSelectedFile(file) {
            selectedName.textContent = file.name;
            selectedDisplay.style.display = 'flex';
            dropZone.style.display = 'none';
        }
        
        clearBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            fileInput.value = '';
            selectedDisplay.style.display = 'none';
            dropZone.style.display = 'flex';
        });
        
        form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const file = fileInput.files[0];
            if (!file) return;
            
            const formData = new FormData();
            formData.append('file_type', type);
            formData.append('file', file);
            
            const btn = document.getElementById(`btn-submit-${type}`);
            btn.disabled = true;
            const origHtml = btn.innerHTML;
            btn.innerHTML = `<i data-lucide="refresh-cw" class="animate-spin"></i> Uploading...`;
            lucide.createIcons();
            
            try {
                const res = await fetch('/api/upload', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    alert(`Successfully uploaded file: ${data.filename}`);
                    // Clear selection
                    fileInput.value = '';
                    selectedDisplay.style.display = 'none';
                    dropZone.style.display = 'flex';
                    loadUploadHistory();
                    loadDashboard();
                } else {
                    alert(`Upload failed: ${data.detail}`);
                }
            } catch (err) {
                alert("Network error occurred during file upload.");
            } finally {
                btn.disabled = false;
                btn.innerHTML = origHtml;
                lucide.createIcons();
            }
        });
    });

    // Load File History table
    async function loadUploadHistory() {
        const tbody = document.getElementById('file-history-body');
        try {
            const res = await fetch('/api/files');
            const files = await res.json();
            
            if (files.length === 0) {
                tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted">No files uploaded yet.</td></tr>`;
                return;
            }

            tbody.innerHTML = files.map(f => {
                const dateStr = new Date(f.uploaded_at).toLocaleString();
                const typeLabel = f.file_type.replace('_', ' ').toUpperCase();
                
                const statusBadge = f.is_latest ? 
                    `<span class="badge badge-success">Latest (In Use)</span>` : 
                    `<span class="badge badge-outline text-muted" style="border: 1px solid var(--border-color); color: #64748B;">Stored</span>`;

                // If it is a template or historical, we can learn mapping from it
                let actionBtn = '';
                if (f.file_type === 'historical_listing' || f.file_type === 'category_template') {
                    actionBtn = `<button class="btn btn-pink py-1 px-2 text-xs" style="margin-right: 6px;" onclick="openLearnModal(${f.id}, '${f.file_type}')">Learn Mappings</button>`;
                }
                actionBtn += `<button class="btn btn-danger py-1 px-2 text-xs" onclick="deleteFile(${f.id})">Delete</button>`;

                return `
                    <tr>
                        <td class="font-semibold">${f.filename}</td>
                        <td><span class="badge badge-warning">${typeLabel}</span></td>
                        <td class="text-muted">${dateStr}</td>
                        <td>${statusBadge}</td>
                        <td>${actionBtn}</td>
                    </tr>
                `;
            }).join('');
            lucide.createIcons();
        } catch (err) {
            tbody.innerHTML = `<tr><td colspan="5" class="text-center text-danger">Failed to load file history.</td></tr>`;
        }
    }

    // Global action helpers (exposed to window for onclicks)

    window.deleteFile = async (id) => {
        if (!confirm("Are you sure you want to delete this file from the database?")) return;
        try {
            const res = await fetch(`/api/files/delete?file_id=${id}`, { method: 'DELETE' });
            const data = await res.json();
            alert(data.message);
            loadUploadHistory();
            loadDashboard();
        } catch (err) {
            alert("Failed to delete file.");
        }
    };

    window.openLearnModal = async (id, fileType) => {
        const category = prompt("Which Nykaa Category does this file represent? (e.g. Tshirts, Westernwear Dresses, shorts, Trousers)");
        if (!category) return;
        
        try {
            const res = await fetch(`/api/learn-historical?file_id=${id}&category=${category}`, { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
                loadDashboard();
            } else {
                alert(`Learning failed: ${data.detail}`);
            }
        } catch (err) {
            alert("Network error occurred during learning trigger.");
        }
    };

    // Dashboard Overview metrics
    async function loadDashboard() {
        try {
            const res = await fetch('/api/files');
            const files = await res.json();
            
            const activeDir = files.find(f => f.file_type === 'item_directory' && f.is_latest);
            const activeContent = files.find(f => f.file_type === 'content_sheet' && f.is_latest);

            document.getElementById('summary-active-directory').textContent = activeDir ? activeDir.filename : "None";
            document.getElementById('summary-active-content').textContent = activeContent ? activeContent.filename : "None";
            
            document.getElementById('metric-directory-name').textContent = activeDir ? activeDir.filename : "None Loaded";
            document.getElementById('metric-directory-time').textContent = activeDir ? `Uploaded ${new Date(activeDir.uploaded_at).toLocaleDateString()}` : "No master sheet";
            
            document.getElementById('metric-content-name').textContent = activeContent ? activeContent.filename : "None Loaded";
            document.getElementById('metric-content-time').textContent = activeContent ? `Uploaded ${new Date(activeContent.uploaded_at).toLocaleDateString()}` : "No content sheet";

            // Load category configs count
            const catRes = await fetch('/api/categories');
            const categories = await catRes.json();
            const activeCats = categories.filter(c => c.has_template);
            document.getElementById('metric-templates-count').textContent = `${activeCats.length} Templates`;

            // Recent Jobs History
            loadRecentJobs();
        } catch (err) {
            console.error(err);
        }
    }

    async function loadRecentJobs() {
        const tbody = document.getElementById('recent-jobs-body');
        // We will fetch from temporary API or local jobs dictionary
        // Since we want recent jobs, we will construct from server status if available or show static
        // Let's call the API to fetch completed jobs (we could add a jobs history API or mock from active runs)
        // For simple UI, we query jobs by listing files generated
        try {
            const res = await fetch('/api/files');
            const files = await res.json();
            const outputFiles = files.filter(f => f.file_type === 'output_file');
            
            if (outputFiles.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" class="text-center text-muted py-4">No jobs run yet. Go to the Listing Generator to start.</td></tr>`;
                return;
            }

            tbody.innerHTML = outputFiles.map((f, index) => {
                const dateStr = new Date(f.uploaded_at).toLocaleString();
                const matchedCat = f.filename.split('_')[2] || "Nykaa SKU";
                return `
                    <tr>
                        <td class="font-mono">JOB_${f.id}</td>
                        <td><span class="font-semibold">${matchedCat}</span></td>
                        <td class="text-muted text-xs">Generated from Directory</td>
                        <td><span class="badge badge-success">Completed</span></td>
                        <td>
                            <a href="/api/download?file_id=${f.id}" class="text-pink font-semibold flex items-center gap-1">
                                <i data-lucide="download" style="width:14px;height:14px;"></i> Download
                            </a>
                        </td>
                        <td class="text-muted">${dateStr}</td>
                    </tr>
                `;
            }).join('');
            lucide.createIcons();
        } catch (err) {
            console.error(err);
        }
    }



    // Generator Form Trigger
    const generatorForm = document.getElementById('generator-form');
    generatorForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const category = 'Kids Clothing Core';
        const inputCodes = document.getElementById('gen-input-codes').value;

        const formData = new FormData();
        formData.append('category', category);
        formData.append('input_codes', inputCodes);

        const btn = document.getElementById('btn-run-generator');
        btn.disabled = true;
        btn.innerHTML = `<i data-lucide="refresh-cw" class="animate-spin"></i> Processing...`;
        
        const progressBar = document.getElementById('gen-progress-bar');
        const badge = document.getElementById('gen-progress-badge');
        progressBar.style.width = '10%';
        progressBar.textContent = '10%';
        badge.textContent = 'Running';
        badge.style.backgroundColor = '#FFF0F5';
        badge.style.color = '#FC2779';

        document.getElementById('result-download-box').style.display = 'none';

        try {
            const res = await fetch('/api/run', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();
            
            if (res.ok) {
                // Poll status
                pollJobStatus(data.job_id);
            } else {
                alert(`Error starting run: ${data.detail}`);
                resetGeneratorBtn();
            }
        } catch (err) {
            alert("Network error starting generation.");
            resetGeneratorBtn();
        }
    });

    function resetGeneratorBtn() {
        const btn = document.getElementById('btn-run-generator');
        btn.disabled = false;
        btn.innerHTML = `<i data-lucide="play"></i> Generate Nykaa Template`;
        const badge = document.getElementById('gen-progress-badge');
        badge.textContent = 'Idle';
        badge.style.backgroundColor = '#F1F5F9';
        badge.style.color = '#64748B';
        lucide.createIcons();
    }

    async function pollJobStatus(jobId) {
        const progressBar = document.getElementById('gen-progress-bar');
        const badge = document.getElementById('gen-progress-badge');

        const interval = setInterval(async () => {
            try {
                const res = await fetch(`/api/status?job_id=${jobId}`);
                const data = await res.json();
                
                progressBar.style.width = `${data.progress}%`;
                progressBar.textContent = `${data.progress}%`;

                if (data.status === 'success') {
                    clearInterval(interval);
                    badge.textContent = 'Success';
                    badge.style.backgroundColor = '#DEF7EC';
                    badge.style.color = '#03543F';
                    
                    // Show download box
                    const dlBox = document.getElementById('result-download-box');
                    dlBox.style.display = 'flex';
                    document.getElementById('download-file-title').textContent = data.output_filename;
                    document.getElementById('btn-download-output').href = `/api/download?file_id=${data.output_file_id}`;
                    
                    resetGeneratorBtn();
                    loadDashboard();
                } else if (data.status === 'failed') {
                    clearInterval(interval);
                    badge.textContent = 'Failed';
                    badge.style.backgroundColor = '#FDE8E8';
                    badge.style.color = '#9B1C1C';
                    alert(`Job failed: ${data.validation_report.error || 'Unknown error'}`);
                    resetGeneratorBtn();
                }
            } catch (err) {
                clearInterval(interval);
                resetGeneratorBtn();
            }
        }, 1500);
    }



    // Learning dropdown and view
    async function loadLearningDropdowns() {
        const select = document.getElementById('learning-category-select');
        try {
            const res = await fetch('/api/categories');
            const categories = await res.json();
            select.innerHTML = `<option value="">-- Choose Category --</option>` + 
                categories.map(c => `<option value="${c.category_name}">${c.category_name}</option>`).join('');
        } catch (err) {
            console.error(err);
        }
    }

    const learningSelect = document.getElementById('learning-category-select');
    learningSelect.addEventListener('change', () => {
        loadLearningConfig(learningSelect.value);
    });

    async function loadLearningConfig(category) {
        const mappingsTbody = document.getElementById('learning-mappings-body');
        const hardcodedTbody = document.getElementById('learning-hardcoded-body');

        if (!category) {
            mappingsTbody.innerHTML = `<tr><td colspan="2" class="text-center text-muted">Select a category above.</td></tr>`;
            hardcodedTbody.innerHTML = `<tr><td colspan="2" class="text-center text-muted">Select a category above.</td></tr>`;
            return;
        }

        try {
            const res = await fetch('/api/categories');
            const categories = await res.json();
            const cat = categories.find(c => c.category_name === category);

            if (!cat) return;

            // Render Column Mappings
            if (Object.keys(cat.column_mappings).length === 0) {
                mappingsTbody.innerHTML = `<tr><td colspan="2" class="text-center text-muted">No mappings learned yet. Go to Upload Center and upload a template or historical sheet.</td></tr>`;
            } else {
                mappingsTbody.innerHTML = Object.entries(cat.column_mappings).map(([k, v]) => `
                    <tr>
                        <td class="font-semibold">${k}</td>
                        <td class="font-mono text-pink">${v}</td>
                    </tr>
                `).join('');
            }

            // Render Hardcoded default values
            if (Object.keys(cat.hardcoded_values).length === 0) {
                hardcodedTbody.innerHTML = `<tr><td colspan="2" class="text-center text-muted">No hardcoded default values learned yet.</td></tr>`;
            } else {
                hardcodedTbody.innerHTML = Object.entries(cat.hardcoded_values).map(([k, v]) => `
                    <tr>
                        <td class="font-semibold">${k}</td>
                        <td class="font-mono text-muted">${v}</td>
                    </tr>
                `).join('');
            }
        } catch (err) {
            console.error(err);
        }
    }

    // Pre-Listing integrity check / Diagnostics validation
    const btnValidation = document.getElementById('btn-run-validation');
    btnValidation.addEventListener('click', async () => {
        btnValidation.disabled = true;
        btnValidation.innerHTML = `<i data-lucide="refresh-cw" class="animate-spin"></i> Running Diagnostics...`;
        lucide.createIcons();

        try {
            const filesRes = await fetch('/api/files');
            const files = await filesRes.json();

            const activeDir = files.find(f => f.file_type === 'item_directory' && f.is_latest);
            const activeContent = files.find(f => f.file_type === 'content_sheet' && f.is_latest);
            
            const catsRes = await fetch('/api/categories');
            const categories = await catsRes.json();
            const activeTemplates = categories.filter(c => c.has_template);

            const reportCard = document.getElementById('validation-report-card');
            const statusHeader = document.getElementById('validation-status-header');
            const reportList = document.getElementById('validation-report-list');
            
            reportCard.style.display = 'block';
            reportList.innerHTML = '';

            let hasErrors = false;
            let checks = [];

            // Check Item Directory
            if (!activeDir) {
                hasErrors = true;
                checks.push({ type: 'error', title: 'Master Item Directory Missing', desc: 'No Item Directory sheet loaded in the system database.' });
            } else {
                checks.push({ type: 'success', title: 'Master Item Directory OK', desc: `Current master sheet: ${activeDir.filename}` });
            }

            // Check Content Sheet
            if (!activeContent) {
                hasErrors = true;
                checks.push({ type: 'error', title: 'Catalog Content Sheet Missing', desc: 'No Content Sheet loaded. Titles and descriptions cannot be mapped.' });
            } else {
                checks.push({ type: 'success', title: 'Catalog Content Sheet OK', desc: `Current content sheet: ${activeContent.filename}` });
            }

            // Check category templates
            if (activeTemplates.length === 0) {
                hasErrors = true;
                checks.push({ type: 'error', title: 'No Category Templates Configured', desc: 'No active learned category templates found. Please upload a template in Upload Center.' });
            } else {
                checks.push({ type: 'success', title: `${activeTemplates.length} Categories Templates Configured`, desc: `Learned templates available: ${activeTemplates.map(c => c.category_name).join(', ')}` });
            }

            if (hasErrors) {
                statusHeader.className = "validation-status-pill text-danger";
                statusHeader.innerHTML = `<i data-lucide="x-circle" class="text-danger text-lg"></i> <div><h3 class="font-semibold">Diagnostics Failed</h3><span class="text-sm">Please resolve errors below before generating templates.</span></div>`;
            } else {
                statusHeader.className = "validation-status-pill text-success";
                statusHeader.innerHTML = `<i data-lucide="check-circle" class="text-success text-lg"></i> <div><h3 class="font-semibold">System Diagnostics Healthy</h3><span class="text-sm">Ready for automated sheet building!</span></div>`;
            }

            reportList.innerHTML = checks.map(c => `
                <div class="validation-item ${c.type === 'error' ? 'error-item' : 'success-item'}">
                    <span class="validation-icon">${c.type === 'error' ? '❌' : '✅'}</span>
                    <div>
                        <h4 class="font-semibold ${c.type === 'error' ? 'text-danger' : 'text-success'}">${c.title}</h4>
                        <span class="text-sm text-muted">${c.desc}</span>
                    </div>
                </div>
            `).join('');
            
            lucide.createIcons();
        } catch (err) {
            alert("Validation failed.");
        } finally {
            btnValidation.disabled = false;
            btnValidation.innerHTML = `<i data-lucide="shield-alert"></i> Run System Diagnostics`;
            lucide.createIcons();
        }
    });



    // Initial Load
    loadDashboard();
});
