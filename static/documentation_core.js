"use strict";

// Configure marked for GitHub-flavored markdown
    marked.setOptions({
      breaks: true,
      gfm: true,
      headerIds: true,
      mangle: false,
      pedantic: false
    });

    // Custom renderer for better semantic HTML
    const renderer = new marked.Renderer();

    const slugCounts = new Map();

    const getUniqueSlug = (base) => {
      const count = slugCounts.get(base) || 0;
      slugCounts.set(base, count + 1);
      if (count === 0) return base;
      return `${base}-${count}`;
    };

    // Custom heading renderer with ID anchors
    renderer.heading = (token) => {
      const rawText = token.text.replace(/<[^>]*>/g, '');
      const id = rawText
        .toLowerCase()
        .trim()
        .replace(/[^\w\s-]/g, '')
        .replace(/\s+/g, '-')
        .replace(/-+/g, '-')
        .replace(/^-|-$/g, '');
      const uniqueId = getUniqueSlug(id);
      return `<h${token.depth} id="${uniqueId}">${token.text}</h${token.depth}>\n`;
    };

    // Custom link renderer
    renderer.link = (token) => {
      const href = token.href || '';
      const title = token.title ? ` title="${token.title}"` : '';
      // In-page anchors should scroll within this document.
      if (href.startsWith('#')) {
        return `<a href="${href}"${title}>${token.text}</a>`;
      }
      return `<a href="${href}"${title} target="_blank" rel="noopener noreferrer">${token.text}</a>`;
    };

    // Custom image renderer
    renderer.image = (token) => {
      let src = token.href || token.url || token.src || '';
      if (src.startsWith('http://')) {
        src = src
          .replace(/^http:\/\/img\.shields\.io/i, 'https://img.shields.io')
          .replace(/^http:\/\/img\.badgesize\.io/i, 'https://img.badgesize.io')
          .replace(/^http:\/\/badgesize\.io/i, 'https://badgesize.io');
      }
      const alt = token.text || token.alt || '';
      return `<img src="${src}" alt="${alt}"${token.title ? ` title="${token.title}"` : ''}>`;
    };

    // Custom code block renderer with language support
    renderer.codespan = (token) => {
      return `<code>${token.text}</code>`;
    };

    // GitHub-style alert blocks (e.g., [!WARNING])
    renderer.blockquote = (token) => {
      const raw = token.text || '';
      const lines = raw.split('\n');
      const first = lines[0] ? lines[0].trim() : '';
      const match = first.match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i);
      if (!match) {
        return `<blockquote>${marked.parse(raw)}</blockquote>`;
      }
      const kind = match[1].toLowerCase();
      const title = match[1].toUpperCase();
      const rest = [match[2], ...lines.slice(1)].filter(Boolean).join('\n');
      return `
        <div class="md-alert md-alert-${kind}">
          <div class="md-alert-title">${title}</div>
          <div class="md-alert-body">${marked.parse(rest)}</div>
        </div>
      `;
    };

    renderer.code = (token) => {
      const lang = token.lang || 'plaintext';
      const normalized = normalizeLang(lang);
      const label = escapeHtml(lang || 'text');
      return `
        <div class="code-block">
          <div class="code-header">
            <span class="code-lang">${label}</span>
            <button class="code-copy" type="button" aria-label="Copy code">Copy</button>
          </div>
          <pre><code class="hljs language-${normalized}">${highlightCode(normalized, token.text)}</code></pre>
        </div>
      `;
    };

    marked.setOptions({ renderer });

    const escapeHtml = (value) => {
      return value
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    };

    const normalizeMarkdown = (md) => {
      const lines = String(md || '').split(/\r?\n/);
      let inFence = false;
      return lines.map(line => {
        const fenceMatch = line.match(/^\s*```/);
        if (fenceMatch) {
          inFence = !inFence;
          return line;
        }
        if (!inFence) {
          if (/^\s*!\[[^\]]*]\([^)]+\)\s*$/.test(line)) {
            return line.trimStart();
          }
        }
        return line;
      }).join('\n');
    };

    const forceInlineImages = (md) => {
      const lines = String(md || '').split(/\r?\n/);
      let inFence = false;
      return lines.map(line => {
        const fenceMatch = line.match(/^\s*```/);
        if (fenceMatch) {
          inFence = !inFence;
          return line;
        }
        if (inFence) return line;
        if (line.includes('<img')) return line;

        let out = '';
        let i = 0;
        while (i < line.length) {
          const start = line.indexOf('![', i);
          if (start === -1) {
            out += line.slice(i);
            break;
          }
          out += line.slice(i, start);
          const altEnd = line.indexOf('](', start);
          if (altEnd === -1) {
            out += line.slice(start);
            break;
          }
          const altText = line.slice(start + 2, altEnd);
          let j = altEnd + 2;
          let depth = 1;
          let urlStart = j;
          while (j < line.length && depth > 0) {
            const ch = line[j];
            if (ch === '(') depth += 1;
            if (ch === ')') depth -= 1;
            j += 1;
          }
          if (depth !== 0) {
            out += line.slice(start);
            break;
          }
          const urlRaw = line.slice(urlStart, j - 1).trim();
          let safeUrl = urlRaw;
          if (safeUrl.startsWith('http://')) {
            safeUrl = safeUrl
              .replace(/^http:\/\/img\.shields\.io/i, 'https://img.shields.io')
              .replace(/^http:\/\/img\.badgesize\.io/i, 'https://img.badgesize.io')
              .replace(/^http:\/\/badgesize\.io/i, 'https://badgesize.io');
          }
          out += `<img src="${safeUrl}" alt="${altText}">`;
          i = j;
        }
        return out;
      }).join('\n');
    };

    const resolveReferenceImages = (md) => {
      const lines = String(md || '').split(/\r?\n/);
      const refs = {};
      for (let i = 0; i < lines.length; i += 1) {
        const line = lines[i];
        const def = line.match(/^\s*\[([^\]]+)\]:\s*(<[^>]+>|\S+)?\s*(?:"([^"]+)"|'([^']+)'|\(([^)]+)\))?\s*$/);
        if (!def) continue;
        const key = def[1].toLowerCase();
        let rawUrl = def[2] || '';
        let title = def[3] || def[4] || def[5] || '';

        if (!rawUrl) {
          let j = i + 1;
          while (j < lines.length && /^\s+/.test(lines[j])) {
            const candidate = lines[j].trim();
            if (candidate) {
              rawUrl = candidate;
              break;
            }
            j += 1;
          }
        }

        if (rawUrl) {
          rawUrl = rawUrl.startsWith('<') ? rawUrl.slice(1, -1) : rawUrl;
          refs[key] = { url: rawUrl, title };
        }
      }

      let inFence = false;
      return lines.map(line => {
        const fenceMatch = line.match(/^\s*```/);
        if (fenceMatch) {
          inFence = !inFence;
          return line;
        }
        if (inFence) return line;

        return line
          .replace(/\[!\[([^\]]*)]\[([^\]]*)]]\s*\[([^\]]*)]/g, (m, alt, imgKey, linkKey) => {
            const imgRefKey = (imgKey || alt || '').toLowerCase();
            const imgRef = refs[imgRefKey];
            const linkRefKey = (linkKey || '').toLowerCase();
            const linkRef = refs[linkRefKey];
            if (!imgRef) return m;
            const imgTitle = imgRef.title ? ` "${imgRef.title}"` : '';
            const img = `![${alt}](${imgRef.url}${imgTitle})`;
            if (!linkRef) return img;
            const linkTitle = linkRef.title ? ` "${linkRef.title}"` : '';
            return `[${img}](${linkRef.url}${linkTitle})`;
          })
          .replace(/!\[([^\]]*)]\[([^\]]*)]/g, (m, alt, key) => {
            const refKey = (key || alt || '').toLowerCase();
            const ref = refs[refKey];
            if (!ref) return m;
            const title = ref.title ? ` "${ref.title}"` : '';
            return `![${alt}](${ref.url}${title})`;
          })
          .replace(/!\[([^\]]*)]\[\]/g, (m, alt) => {
            const refKey = (alt || '').toLowerCase();
            const ref = refs[refKey];
            if (!ref) return m;
            const title = ref.title ? ` "${ref.title}"` : '';
            return `![${alt}](${ref.url}${title})`;
          });
      }).join('\n');
    };

    const normalizeLang = (lang) => {
      const normalized = String(lang || '').toLowerCase();
      if (['sh', 'shell', 'bash', 'zsh'].includes(normalized)) {
        return 'bash';
      }
      return normalized || 'plaintext';
    };

    const SANITIZE_ALLOWED_TAGS = new Set([
      'a', 'article', 'blockquote', 'br', 'code', 'del', 'div', 'em',
      'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'img', 'li', 'ol',
      'p', 'pre', 'section', 'span', 'strong', 'table', 'tbody', 'td',
      'th', 'thead', 'tr', 'ul'
    ]);
    const SANITIZE_DROP_WITH_CONTENT = new Set([
      'script', 'style', 'iframe', 'object', 'embed', 'link', 'meta',
      'base', 'form', 'input', 'button', 'textarea', 'select', 'option'
    ]);
    const SANITIZE_GLOBAL_ATTRS = new Set(['class', 'id', 'aria-label', 'role']);
    const SANITIZE_ATTRS_BY_TAG = {
      a: new Set(['href', 'title', 'target', 'rel']),
      img: new Set(['src', 'alt', 'title', 'width', 'height', 'loading', 'decoding']),
      th: new Set(['colspan', 'rowspan']),
      td: new Set(['colspan', 'rowspan']),
      code: new Set(['class']),
      pre: new Set(['class']),
      div: new Set(['class', 'id']),
      span: new Set(['class', 'id'])
    };

    const isSafeUrl = (value, { allowAnchor = false } = {}) => {
      const raw = String(value || '').trim();
      if (!raw) return false;
      if (allowAnchor && raw.startsWith('#')) return true;
      const normalized = raw.replace(/[\u0000-\u001F\u007F\s]+/g, '').toLowerCase();
      if (!normalized) return false;
      if (
        normalized.startsWith('javascript:') ||
        normalized.startsWith('vbscript:') ||
        normalized.startsWith('data:')
      ) {
        return false;
      }
      if (
        normalized.startsWith('http://') ||
        normalized.startsWith('https://') ||
        normalized.startsWith('mailto:')
      ) {
        return true;
      }
      return !normalized.includes(':');
    };

    const sanitizeRenderedHtml = (html) => {
      const template = document.createElement('template');
      template.innerHTML = String(html || '');

      const walk = (node) => {
        if (!node) return;

        if (node.nodeType === Node.ELEMENT_NODE) {
          const el = node;
          const tag = el.tagName.toLowerCase();

          if (!SANITIZE_ALLOWED_TAGS.has(tag)) {
            if (SANITIZE_DROP_WITH_CONTENT.has(tag)) {
              el.remove();
              return;
            }
            const parent = el.parentNode;
            if (parent) {
              while (el.firstChild) {
                parent.insertBefore(el.firstChild, el);
              }
              parent.removeChild(el);
            } else {
              el.remove();
            }
            return;
          }

          const allowedAttrs = SANITIZE_ATTRS_BY_TAG[tag] || new Set();
          Array.from(el.attributes).forEach((attr) => {
            const name = attr.name.toLowerCase();
            const value = attr.value || '';

            if (name.startsWith('on')) {
              el.removeAttribute(attr.name);
              return;
            }
            if (!SANITIZE_GLOBAL_ATTRS.has(name) && !allowedAttrs.has(name)) {
              el.removeAttribute(attr.name);
              return;
            }
            if (name === 'href' && !isSafeUrl(value, { allowAnchor: true })) {
              el.removeAttribute(attr.name);
              return;
            }
            if (name === 'src' && !isSafeUrl(value)) {
              el.removeAttribute(attr.name);
              return;
            }
          });

          if (tag === 'a' && el.getAttribute('target') === '_blank') {
            el.setAttribute('rel', 'noopener noreferrer');
          }
        }

        Array.from(node.childNodes).forEach(walk);
      };

      Array.from(template.content.childNodes).forEach(walk);
      return template.innerHTML;
    };

    const highlightCode = (lang, code) => {
      const normalized = normalizeLang(lang);
      if (window.hljs) {
        if (normalized && hljs.getLanguage(normalized)) {
          const highlighted = hljs.highlight(code, { language: normalized }).value;
          if (normalized === 'bash') {
            return enhanceShellHighlight(highlighted);
          }
          return highlighted;
        }
        return hljs.highlightAuto(code).value;
      }
      return escapeHtml(code);
    };

    const enhanceShellHighlight = (html) => {
      return html.split('\n').map(line => {
        if (line.includes('<span')) return line;
        const withCommand = line.replace(/^(\s*)([^\s#]+)/, '$1<span class="tok-command">$2</span>');
        return withCommand.replace(/(\s)(-{1,2}[\w-]+)/g, '$1<span class="tok-flag">$2</span>');
      }).join('\n');
    };

    const applySystemTheme = () => {
      const themeLink = document.getElementById('hljs-theme');
      const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
      document.documentElement.classList.toggle('theme-dark', !!prefersDark);
      if (!themeLink) return;
      const nextHref = prefersDark ? themeLink.dataset.dark : themeLink.dataset.light;
      if (nextHref && themeLink.getAttribute('href') !== nextHref) {
        themeLink.setAttribute('href', nextHref);
      }
    };

    applySystemTheme();
    if (window.matchMedia) {
      const media = window.matchMedia('(prefers-color-scheme: dark)');
      if (typeof media.addEventListener === 'function') {
        media.addEventListener('change', applySystemTheme);
      } else if (typeof media.addListener === 'function') {
        media.addListener(applySystemTheme);
      }
    }

    // Fetch and render README
    const defaultReadmeURL = '/doc/server_setup_doc.md';
    const contentElement = document.getElementById('content');
    const backToTopButton = document.getElementById('backToTop');
    const stickyHeader = document.getElementById('stickyHeader');
    const stickyHeaderTitle = document.getElementById('stickyHeaderTitle');
      const stickyMenuButton = document.getElementById('stickyMenu');
      const stickyMenuLabel = document.getElementById('stickyMenuLabel');
    const tocSidebar = document.getElementById('tocSidebar');
    const tocSidebarBody = document.getElementById('tocSidebarBody');
    const navToggle = document.getElementById('nav-toggle');
    const sideNav = document.getElementById('side-nav');
    const navBackdrop = document.getElementById('nav-backdrop');
    const initMcwebNav = () => {
      if (!navToggle || !sideNav || !navBackdrop) return;

      const closeNav = () => {
        sideNav.classList.remove('open');
        navBackdrop.classList.remove('open');
        navToggle.classList.remove('nav-open');
        navToggle.setAttribute('aria-expanded', 'false');
      };

      const toggleNav = () => {
        const nextOpen = !sideNav.classList.contains('open');
        sideNav.classList.toggle('open', nextOpen);
        navBackdrop.classList.toggle('open', nextOpen);
        navToggle.classList.toggle('nav-open', nextOpen);
        navToggle.setAttribute('aria-expanded', nextOpen ? 'true' : 'false');
      };

      navToggle.addEventListener('click', toggleNav);
      navBackdrop.addEventListener('click', closeNav);
      window.addEventListener('resize', () => {
        if (window.innerWidth > 1100) closeNav();
      });
    };

    const addMediaListener = (mql, handler) => {
      if (!mql || !handler) return;
      if (typeof mql.addEventListener === 'function') {
        mql.addEventListener('change', handler);
      } else if (typeof mql.addListener === 'function') {
        mql.addListener(handler);
      }
    };

    const bindAnchorNavigation = (root, onNavigate) => {
      if (!root || !onNavigate) return;
      root.querySelectorAll('a[href^="#"]').forEach(link => {
        link.addEventListener('click', (event) => {
          const targetId = (link.getAttribute('href') || '').slice(1);
          if (!targetId) return;
          const target = document.getElementById(targetId);
          if (!target) return;
          event.preventDefault();
          onNavigate(target, targetId);
        });
      });
    };

      const processMarkdown = (mdText) => {
      let updateSticky = () => {};
      let updateActiveTocLink = () => {};
      let headings = [];
      let ticking = false;
      slugCounts.clear();
      const resolved = resolveReferenceImages(mdText);
      const normalized = normalizeMarkdown(resolved);
      const html = sanitizeRenderedHtml(marked.parse(forceInlineImages(normalized)));
      contentElement.innerHTML = html;

      const h1Title = contentElement.querySelector('h1');
      if (h1Title) {
        document.title = h1Title.textContent.trim();
      }

      // Remove in-content "Back to top" links from the README.
      const normalize = (value) => value
        .replace(/\s+/g, ' ')
        .replace(/[^\w\s]/g, '')
        .trim()
        .toLowerCase();

      const backToTopTargets = new Set(['#top', '#table-of-contents', '#toc', '#readme']);
      const backToTopLabels = [
        'back to top',
        'back to the top',
        'back to start',
        'back to contents',
        'back to table of contents',
        'return to top',
        'return to the top',
        'top'
      ].map(normalize);

      contentElement.querySelectorAll('a[href^="#"]').forEach(link => {
        const href = (link.getAttribute('href') || '').toLowerCase();
        const label = normalize(link.textContent || '');
        const isBackToTop = backToTopTargets.has(href) || backToTopLabels.includes(label);
        if (!isBackToTop) return;
        const parent = link.parentElement;
        link.remove();
        if (parent && parent.textContent.trim() === '') {
          parent.remove();
        }
      });

      const mergeBadgeRows = () => {
        const paragraphs = Array.from(contentElement.querySelectorAll('p'));
        paragraphs.forEach(p => {
          const nodes = Array.from(p.childNodes).filter(node => {
            if (node.nodeType === Node.TEXT_NODE) {
              return node.textContent.trim().length > 0;
            }
            return node.nodeType === Node.ELEMENT_NODE && node.tagName !== 'BR';
          });
          if (nodes.length === 0) return;
          const allBadges = nodes.every(node => {
            if (node.nodeType !== Node.ELEMENT_NODE) return false;
            if (node.tagName === 'IMG') return true;
            if (node.tagName === 'A') {
              return !!node.querySelector('img');
            }
            return false;
          });
          if (!allBadges) return;
          const wrapper = document.createElement('div');
          wrapper.className = 'badge-row';
          nodes.forEach(node => wrapper.appendChild(node));
          p.replaceWith(wrapper);
        });
      };

      const wireCopyButtons = () => {
        const buttons = contentElement.querySelectorAll('.code-copy');
        buttons.forEach(button => {
          button.addEventListener('click', async () => {
            const codeEl = button.closest('.code-block')?.querySelector('pre code');
            if (!codeEl) return;
            const text = codeEl.textContent || '';
            try {
              if (navigator.clipboard?.writeText) {
                await navigator.clipboard.writeText(text);
              } else {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                textarea.remove();
              }
              const original = button.textContent;
              button.textContent = 'Copied';
              button.disabled = true;
              setTimeout(() => {
                button.textContent = original;
                button.disabled = false;
              }, 1200);
            } catch (err) {
              console.error('Copy failed:', err);
            }
          });
        });
      };

      mergeBadgeRows();
      wireCopyButtons();

      // Always point the floating button to top.
      backToTopButton.setAttribute('href', '#top');
      backToTopButton.textContent = 'Back to top';
      backToTopButton.addEventListener('click', (event) => {
        event.preventDefault();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      });

      let tocPinned = true;
      let updateTocVisibility = () => {};
      const narrowMql = window.matchMedia('(max-width: 1100px)');
      const wideMql = window.matchMedia('(min-width: 1101px)');

      const syncStickyOffset = () => {
        const offset = (stickyHeader?.offsetHeight || 0) + 12;
        document.documentElement.style.setProperty('--sticky-offset', `${offset}px`);
      };

      const navigateToHeading = (target, targetId) => {
        scrollToTarget(target);
        history.pushState(null, '', `#${targetId}`);
      };

      const scrollToTarget = (target) => {
        if (!target) return;
        const offset = (stickyHeader?.offsetHeight || 0) + 12;
        const targetTop = target.getBoundingClientRect().top + window.scrollY - offset;
        window.scrollTo({ top: targetTop, behavior: 'smooth' });
      };

      const buildTocListFromHeadings = () => {
        const list = document.createElement('ul');
        const headingNodes = Array.from(contentElement.querySelectorAll('h2, h3'));
        headingNodes.forEach(heading => {
          if (heading.id === 'table-of-contents') return;
          const link = document.createElement('a');
          link.href = `#${heading.id}`;
          link.textContent = heading.textContent.trim();
          link.classList.add('toc-item', heading.tagName === 'H3' ? 'level-3' : 'level-2');
          const item = document.createElement('li');
          item.appendChild(link);
          list.appendChild(item);
        });
        return list;
      };

      const findInlineToc = () => {
        const candidates = Array.from(contentElement.querySelectorAll('ul, ol, p, div, section, nav'));
        let best = null;
        let bestScore = 0;

        candidates.forEach(node => {
          const anchors = node.querySelectorAll('a[href^="#"]');
          if (anchors.length < 4) return;
          const text = (node.textContent || '').trim();
          const wordCount = text ? text.split(/\s+/).length : 0;
          const density = anchors.length / Math.max(1, wordCount);
          const score = anchors.length + density * 8;
          if (score > bestScore) {
            bestScore = score;
            best = node;
          }
        });

        return best;
      };

      const inlineTocVisible = () => {
        const tocNode = findInlineToc();
        if (!tocNode) return;
        const findTocHeading = (node) => {
          let cursor = node.previousElementSibling;
          while (cursor) {
            if (/^H[1-6]$/.test(cursor.tagName)) {
              return cursor;
            }
            cursor = cursor.previousElementSibling;
          }
          return null;
        };
        const tocHeading = findTocHeading(tocNode);
        const shouldHide = wideMql.matches;
        tocNode.style.display = shouldHide ? 'none' : '';
        if (tocHeading) {
          tocHeading.style.display = shouldHide ? 'none' : '';
        }
      };

      // Build TOC sidebar from document headings (2 levels max).
      const buildTocSidebar = () => {
        const listEl = buildTocListFromHeadings();

        tocSidebarBody.innerHTML = '';
        tocSidebarBody.appendChild(listEl);
        bindAnchorNavigation(tocSidebarBody, navigateToHeading);

        updateTocVisibility = () => {
          const visible = tocPinned;
          tocSidebar.classList.toggle('is-visible', visible);
          stickyMenuLabel.hidden = !visible;
          stickyMenuButton.setAttribute('aria-expanded', (!stickyMenuButton.hidden && visible) ? 'true' : 'false');
          if (narrowMql.matches) {
            stickyMenuButton.classList.toggle('is-open', visible);
          } else {
            stickyMenuButton.classList.remove('is-open');
          }
        };

        updateTocVisibility();
      };

      stickyMenuButton.addEventListener('click', () => {
        if (narrowMql.matches) {
          tocPinned = !tocPinned;
        } else {
          tocPinned = true;
        }
        updateTocVisibility();
      });

      const syncTocForWidth = () => {
        if (narrowMql.matches) {
          tocPinned = false;
          stickyMenuButton.hidden = false;
          stickyMenuButton.classList.remove('is-open');
        } else {
          tocPinned = true;
          stickyMenuButton.hidden = true;
          stickyMenuButton.classList.remove('is-open');
          stickyMenuButton.setAttribute('aria-expanded', 'false');
        }
        updateTocVisibility();
        inlineTocVisible();
      };
      syncTocForWidth();
      addMediaListener(narrowMql, syncTocForWidth);
      addMediaListener(wideMql, inlineTocVisible);

      buildTocSidebar();

      // Enable in-page anchor navigation after content is injected.
      bindAnchorNavigation(contentElement, navigateToHeading);

      // If the page loads with a hash, scroll to it.
      if (location.hash) {
        const target = document.getElementById(location.hash.slice(1));
        if (target) {
          scrollToTarget(target);
        }
      }

      const shouldTrackHeading = (heading) => {
        if (!heading) return false;
        if (heading.id === 'table-of-contents') return false;
        if (heading.tagName === 'H2' && heading.textContent.trim().toLowerCase() === 'table of contents') {
          return false;
        }
        const style = window.getComputedStyle(heading);
        return style.display !== 'none' && style.visibility !== 'hidden';
      };

      // Sticky section header (H2) with current subsection (H3).
      headings = Array.from(contentElement.querySelectorAll('h1, h2, h3')).filter(shouldTrackHeading);

      updateActiveTocLink = (currentH2, currentH3) => {
        const links = tocSidebarBody.querySelectorAll('a[href^="#"]');
        links.forEach(link => link.classList.remove('is-active'));
        const activeHeading = currentH3 || currentH2;
        if (!activeHeading) {
          const firstLink = links[0];
          if (firstLink) {
            firstLink.classList.add('is-active');
            firstLink.scrollIntoView({ block: 'nearest', inline: 'nearest' });
          }
          return;
        }
        const activeLink = tocSidebarBody.querySelector(`a[href="#${activeHeading.id}"]`);
        if (activeLink) {
          activeLink.classList.add('is-active');
          activeLink.scrollIntoView({ block: 'nearest', inline: 'nearest' });
        }
      };

      updateSticky = () => {
        ticking = false;
        let currentH1 = null;
        let currentH2 = null;
        let currentH3 = null;
        const scrollY = window.scrollY + (stickyHeader?.offsetHeight || 0) + 12;

        for (const heading of headings) {
          if (!shouldTrackHeading(heading)) continue;
          if (heading.offsetTop <= scrollY) {
            if (heading.tagName === 'H1') {
              currentH1 = heading;
              currentH2 = null;
              currentH3 = null;
            } else if (heading.tagName === 'H2') {
              currentH2 = heading;
              currentH3 = null;
            } else if (heading.tagName === 'H3' && currentH2) {
              currentH3 = heading;
            }
          }
        }

        const stripLeadingNumber = (text) => {
          return text.replace(/^\s*\d+(?:\.\d+)*\s*[-.)]?\s*/u, '');
        };

        const h1Text = currentH1 ? currentH1.textContent.trim() : '';
        const h2Text = currentH2 ? stripLeadingNumber(currentH2.textContent.trim()) : '';
        const h3Text = currentH3 ? stripLeadingNumber(currentH3.textContent.trim()) : '';
        const title = h3Text
          ? `${h2Text}: ${h3Text}`
          : (h2Text || h1Text || '');
        stickyHeaderTitle.textContent = title || '\u00a0';
        stickyHeader.hidden = false;
        updateActiveTocLink(currentH2, currentH3);
      };

      syncStickyOffset();
      updateTocVisibility();
      updateSticky();
      window.addEventListener('resize', syncStickyOffset);
      window.addEventListener('scroll', () => {
        if (!ticking) {
          window.requestAnimationFrame(updateSticky);
          ticking = true;
        }
      }, { passive: true });
    };

    const loadReadmeFromUrl = (url) => {
      if (!url) return;
      fetch(url)
        .then(res => {
          if (!res.ok) {
            throw new Error(`Network error ${res.status}: ${res.statusText}`);
          }
          return res.text();
        })
        .then(processMarkdown)
        .catch(err => {
          contentElement.textContent = `Error loading README: ${err.message}`;
          console.error('Failed to load README:', err);
        });
    };

    const loadConfiguredReadme = () => {
      fetch('/doc/readme-url', { cache: 'no-store' })
        .then(res => {
          if (!res.ok) {
            throw new Error(`Config endpoint error ${res.status}`);
          }
          return res.json();
        })
        .then(payload => {
          const configuredUrl = (payload && payload.url) ? String(payload.url).trim() : '';
          loadReadmeFromUrl(configuredUrl || defaultReadmeURL);
        })
        .catch(() => {
          loadReadmeFromUrl(defaultReadmeURL);
        });
    };

    window.startDocumentationPage = () => {
      contentElement.innerHTML = '';
      initMcwebNav();
      loadConfiguredReadme();
    };
