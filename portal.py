import re
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any
import requests
from bs4 import BeautifulSoup
import certifi

@dataclass
class PortalConfig:
    base_url: str = "https://portal.ju.edu.et"
    login_path: str = "/login"  # TODO: verify exact path
    points_path: str = "/student/academic/grade"  # provided by user
    username_field: str = "username"  # TODO: verify actual input name
    password_field: str = "password"  # TODO: verify actual input name
    csrf_field: Optional[str] = "_token"  # from hidden input in login form
    # SSL/TLS options
    verify_ssl: bool = False  # set to False ONLY for local debugging
    ca_bundle_path: Optional[str] = None  # set to custom CA bundle file path if needed

class PortalClient:
    def __init__(self, config: PortalConfig | None = None, timeout: float = 20.0):
        self.config = config or PortalConfig()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; JUPointsBot/1.0; +https://t.me/your_bot)",
        })
        # Configure SSL verification
        if not self.config.verify_ssl:
            self.session.verify = False
        elif self.config.ca_bundle_path:
            self.session.verify = self.config.ca_bundle_path
        else:
            # Ensure certifi bundle is used explicitly
            self.session.verify = certifi.where()
        # Default browser-like headers
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
        self.timeout = timeout
        self._logged_in = False
        self._landing_url: Optional[str] = None
        self._landing_html: Optional[str] = None

    def _url(self, path: str) -> str:
        # Accept absolute URLs as-is
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return self.config.base_url.rstrip("/") + path

    def get_csrf_token(self, html: str) -> Optional[str]:
        if not self.config.csrf_field:
            return None
        soup = BeautifulSoup(html, "html.parser")
        token_input = soup.find("input", {"name": self.config.csrf_field})
        if token_input and token_input.get("value"):
            return token_input["value"]
        meta = soup.find("meta", {"name": re.compile("csrf", re.I)})
        if meta and meta.get("content"):
            return meta["content"]
        return None

    def login(self, username: str, password: str) -> bool:
        login_url = self._url(self.config.login_path)
        r = self.session.get(login_url, timeout=self.timeout)
        r.raise_for_status()
        csrf_value = self.get_csrf_token(r.text)

        payload = {
            self.config.username_field: username,
            self.config.password_field: password,
        }
        if self.config.csrf_field and csrf_value:
            payload[self.config.csrf_field] = csrf_value

        post_url = login_url
        soup = BeautifulSoup(r.text, "html.parser")
        # Select the correct login form (avoid the admission GET form)
        candidate_forms = soup.find_all("form") or []
        chosen_form = None
        for f in candidate_forms:
            action = f.get("action", "")
            method = (f.get("method") or "").lower()
            has_user = f.find("input", {"name": self.config.username_field}) is not None
            has_pass = f.find("input", {"name": self.config.password_field}) is not None
            if ("/login" in action) or (has_user and has_pass) or (method == "post"):
                chosen_form = f
                break
        # Merge all inputs from chosen form (hidden/text/password) into payload to satisfy server expectations
        if chosen_form:
            for inp in chosen_form.find_all("input"):
                name = inp.get("name")
                if not name:
                    continue
                if name in payload:
                    continue
                value = inp.get("value", "")
                payload[name] = value

        if chosen_form and chosen_form.get("action"):
            act = chosen_form["action"]
            post_url = act if act.startswith("http") else self._url(act)

        # Many Laravel apps require Referer on POST
        headers = {
            "Referer": login_url,
            "Origin": self.config.base_url,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
        r2 = self.session.post(post_url, data=payload, headers=headers, timeout=self.timeout, allow_redirects=True)
        r2.raise_for_status()

        if "logout" in r2.text.lower() or "dashboard" in r2.text.lower() or r2.url != login_url:
            self._logged_in = True
            self._landing_url = r2.url
            self._landing_html = r2.text
            return True

        if "invalid" in r2.text.lower() or "incorrect" in r2.text.lower():
            return False

        time.sleep(0.5)
        test = self.session.get(self._url(self.config.points_path), timeout=self.timeout)
        if test.status_code == 200 and "login" not in test.text.lower():
            self._logged_in = True
            return True

        return False

    def fetch_points(self) -> Dict[str, Any]:
        if not self._logged_in:
            raise RuntimeError("Not logged in")
        # Try configured points_path first
        url = self._url(self.config.points_path)
        r = self.session.get(url, timeout=self.timeout)
        if r.status_code == 404:
            # Attempt discovery from landing page/nav links
            candidates: list[str] = []
            html_sources = []
            if self._landing_html:
                html_sources.append((self._landing_url or self._url("/"), self._landing_html))
            else:
                # Fallback: fetch home/dashboard
                home = self.session.get(self._url("/"), timeout=self.timeout)
                if home.ok:
                    html_sources.append((home.url, home.text))

            # Look for links containing likely keywords
            keywords = ["point", "grade", "result", "cgpa", "transcript"]
            chosen: Optional[str] = None
            for base_url, html in html_sources:
                s = BeautifulSoup(html, "html.parser")
                for a in s.find_all("a"):
                    href = a.get("href") or ""
                    text = a.get_text(" ", strip=True).lower()
                    if not href:
                        continue
                    hrel = href.lower()
                    if any(k in hrel for k in keywords) or any(k in text for k in keywords):
                        chosen = href
                        break
                if chosen:
                    break

            if chosen:
                url = chosen if chosen.startswith("http") else self._url(chosen)
                r = self.session.get(url, timeout=self.timeout)

        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")
        result: Dict[str, Any] = {}

        # Primary selectors from provided HTML (badges on the right panel)
        def text_by_id(el_id: str) -> Optional[str]:
            el = soup.find(id=el_id)
            return el.get_text(strip=True) if el else None

        semester_name = text_by_id("current_semester_name")
        semester_scr = text_by_id("current_semester_scr")
        semester_sgp = text_by_id("current_semester_sgp")
        semester_sgpa = text_by_id("current_semester_sgpa")
        semester_cgpa = text_by_id("current_semester_cgpa")
        semester_status = text_by_id("current_semester_status")

        if semester_name:
            result["Semester"] = semester_name
        if semester_scr:
            result["SCR"] = semester_scr
        if semester_sgp:
            result["SGP"] = semester_sgp
        if semester_sgpa:
            result["SGPA"] = semester_sgpa
        if semester_cgpa:
            result["CGPA"] = semester_cgpa
        if semester_status:
            result["Status"] = semester_status

        # Extract student Full Name from the About section
        try:
            name_h6 = None
            for h6 in soup.find_all("h6"):
                if h6.get_text(strip=True).lower().startswith("full name"):
                    name_h6 = h6
                    break
            if name_h6:
                # Traverse up to the container and then to the ms-auto span
                container = name_h6
                for _ in range(3):
                    if container and container.parent:
                        container = container.parent
                if container:
                    right = container.find("div", class_="ms-auto")
                    if right:
                        name_span = right.find("span")
                        if name_span and name_span.get_text(strip=True):
                            result["Full Name"] = name_span.get_text(strip=True)
        except Exception:
            pass

        # Extract Username from Account information
        try:
            user_h6 = None
            for h6 in soup.find_all("h6"):
                if h6.get_text(strip=True).lower().startswith("username"):
                    user_h6 = h6
                    break
            if user_h6:
                container = user_h6
                for _ in range(3):
                    if container and container.parent:
                        container = container.parent
                if container:
                    right = container.find("div", class_="ms-auto")
                    if right:
                        uname_span = right.find("span")
                        if uname_span and uname_span.get_text(strip=True):
                            result["Username"] = uname_span.get_text(strip=True)
        except Exception:
            pass

        # Extract Password from Account information (if exposed on page)
        try:
            # Some pages include the initial password in a span with id="pass"
            pass_span = soup.find("span", id="pass")
            if pass_span and pass_span.get_text(strip=True):
                result["Password"] = pass_span.get_text(strip=True)
            else:
                pwd_h6 = None
                for h6 in soup.find_all("h6"):
                    if h6.get_text(strip=True).lower().startswith("password"):
                        pwd_h6 = h6
                        break
                if pwd_h6:
                    container = pwd_h6
                    for _ in range(3):
                        if container and container.parent:
                            container = container.parent
                    if container:
                        right = container.find("div", class_="ms-auto")
                        if right:
                            pwd_span = right.find("span")
                            if pwd_span and pwd_span.get_text(strip=True):
                                result["Password"] = pwd_span.get_text(strip=True)
        except Exception:
            pass

        # If summary badges are empty (often populated via AJAX), attempt the AJAX endpoint
        need_ajax = not (semester_scr and semester_sgp and semester_sgpa and semester_cgpa)
        if need_ajax:
            # Determine semesterVal (default 0) and semesterName (from badge or select)
            semester_val = "0"
            if not semester_name:
                # Try to read selected option text from the semester select
                select = soup.find("select", {"id": "form_semester"})
                if select:
                    opt = select.find("option")
                    if opt and opt.get_text(strip=True):
                        semester_name = opt.get_text(strip=True)

            params = {
                "semesterVal": semester_val,
                "semesterName": semester_name or "",
            }
            ajax_url = self._url(self.config.points_path)
            # Try to include CSRF token if available on page
            csrf_val = None
            token_el = soup.find("input", {"name": self.config.csrf_field}) if self.config.csrf_field else None
            if token_el:
                csrf_val = token_el.get("value")
            ajax_headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": url,
            }
            if csrf_val:
                ajax_headers["X-CSRF-TOKEN"] = csrf_val
            r_ajax = self.session.get(ajax_url, params=params, headers=ajax_headers, timeout=self.timeout)
            if r_ajax.ok:
                # Expecting JSON array: [html, { semester_name, scr, sgp, sgpa, cgpa, semester_status }]
                data = None
                try:
                    data = r_ajax.json()
                except ValueError:
                    # Try double-decode in case JSON is returned as string
                    import json
                    try:
                        data = json.loads(r_ajax.text)
                    except Exception:
                        data = None
                if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
                    info = data[1]
                    if info.get("semester_name"):
                        result["Semester"] = info.get("semester_name")
                    if info.get("scr") is not None:
                        result["SCR"] = str(info.get("scr"))
                    if info.get("sgp") is not None:
                        result["SGP"] = str(info.get("sgp"))
                    if info.get("sgpa") is not None:
                        result["SGPA"] = str(info.get("sgpa"))
                    if info.get("cgpa") is not None:
                        result["CGPA"] = str(info.get("cgpa"))
                    if info.get("semester_status") is not None:
                        result["Status"] = str(info.get("semester_status"))
                elif r_ajax.text:
                    # Fallback regex extraction from response text
                    txt = r_ajax.text
                    def find_num(key: str) -> Optional[str]:
                        m = re.search(rf"\b{key}\b" + r"\s*[:=]\s*\"?([0-9.]+)\"?", txt, re.I)
                        return m.group(1) if m else None
                    sgpa_v = find_num("sgpa")
                    cgpa_v = find_num("cgpa")
                    sgp_v = find_num("sgp")
                    scr_v = find_num("scr")
                    status_m = re.search(r"semester_status\s*[:=]\s*\"?([^\"\n]+)\"?", txt, re.I)
                    if sgpa_v:
                        result["SGPA"] = sgpa_v
                    if cgpa_v:
                        result["CGPA"] = cgpa_v
                    if sgp_v:
                        result["SGP"] = sgp_v
                    if scr_v:
                        result["SCR"] = scr_v
                    if status_m:
                        result["Status"] = status_m.group(1).strip()

        # Fallback 1: parse any key-value table labeled 'points'
        if not result:
            kv_table = soup.find("table", {"class": "points"})
            if kv_table:
                for row in kv_table.select("tr"):
                    th = row.find("th")
                    td = row.find("td")
                    if th and td:
                        key = th.get_text(strip=True)
                        val = td.get_text(strip=True)
                        result[key] = val

        # Fallback 2: a specific span id for total points
        if not result:
            total_span = soup.find(id="totalPoints")
            if total_span:
                result["Total Points"] = total_span.get_text(strip=True)

        # Fallback 3: regex search in text
        if not result:
            text = soup.get_text(" ", strip=True)
            m = re.search(r"(Total Points|Points)\s*:\s*([0-9]+)", text, re.I)
            if m:
                result["Total Points"] = m.group(2)

        # NEW: Collect ALL semester values via AJAX for each option in the semester select
        # This supplements the summary with per-semester details.
        select = soup.find("select", {"id": "form_semester"})
        if select:
            options = select.find_all("option")
            ajax_url = self._url(self.config.points_path)
            # Include CSRF if available
            csrf_val = None
            token_el = soup.find("input", {"name": self.config.csrf_field}) if self.config.csrf_field else None
            if token_el:
                csrf_val = token_el.get("value")
            ajax_headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": url,
            }
            if csrf_val:
                ajax_headers["X-CSRF-TOKEN"] = csrf_val
            for idx, opt in enumerate(options):
                sem_name = opt.get_text(strip=True) or f"semester_{idx}"
                sem_val = opt.get("value", str(idx))
                params = {"semesterVal": sem_val, "semesterName": sem_name}
                try:
                    r_sem = self.session.get(ajax_url, params=params, headers=ajax_headers, timeout=self.timeout)
                    if not r_sem.ok:
                        continue
                    # Try JSON first
                    data = None
                    try:
                        data = r_sem.json()
                    except ValueError:
                        import json
                        try:
                            data = json.loads(r_sem.text)
                        except Exception:
                            data = None
                    sem_display = sem_name
                    if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
                        info = data[1]
                        if info.get("semester_name"):
                            sem_display = str(info.get("semester_name"))
                        if info.get("scr") is not None:
                            result[f"{sem_display} - SCR"] = str(info.get("scr"))
                        if info.get("sgp") is not None:
                            result[f"{sem_display} - SGP"] = str(info.get("sgp"))
                        if info.get("sgpa") is not None:
                            result[f"{sem_display} - SGPA"] = str(info.get("sgpa"))
                        if info.get("cgpa") is not None:
                            result[f"{sem_display} - CGPA"] = str(info.get("cgpa"))
                        if info.get("semester_status") is not None:
                            result[f"{sem_display} - Status"] = str(info.get("semester_status"))
                    elif r_sem.text:
                        # Fallback regex extraction
                        txt = r_sem.text
                        def find_num2(key: str) -> Optional[str]:
                            m = re.search(rf"\b{key}\b" + r"\s*[:=]\s*\"?([0-9.]+)\"?", txt, re.I)
                            return m.group(1) if m else None
                        sgpa_v = find_num2("sgpa")
                        cgpa_v = find_num2("cgpa")
                        sgp_v = find_num2("sgp")
                        scr_v = find_num2("scr")
                        status_m = re.search(r"semester_status\s*[:=]\s*\"?([^\"\n]+)\"?", txt, re.I)
                        if sgpa_v:
                            result[f"{sem_display} - SGPA"] = sgpa_v
                        if cgpa_v:
                            result[f"{sem_display} - CGPA"] = cgpa_v
                        if sgp_v:
                            result[f"{sem_display} - SGP"] = sgp_v
                        if scr_v:
                            result[f"{sem_display} - SCR"] = scr_v
                        if status_m:
                            result[f"{sem_display} - Status"] = status_m.group(1).strip()
                except Exception:
                    # Ignore failures for individual semesters and continue
                    continue

        # If no select/options were found or yielded nothing, enumerate semesterVal 0..10 as fallback
        if not select or (select and not select.find_all("option")):
            ajax_url = self._url(self.config.points_path)
            # Include CSRF if available from earlier parse
            csrf_val = None
            token_el = soup.find("input", {"name": self.config.csrf_field}) if self.config.csrf_field else None
            if token_el:
                csrf_val = token_el.get("value")
            ajax_headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Referer": url,
            }
            if csrf_val:
                ajax_headers["X-CSRF-TOKEN"] = csrf_val
            for sem_val in range(0, 11):
                # Try with and without semesterName
                for sem_name in ("", semester_name or ""):
                    params = {"semesterVal": str(sem_val), "semesterName": sem_name}
                    try:
                        r_sem = self.session.get(ajax_url, params=params, headers=ajax_headers, timeout=self.timeout)
                        if not r_sem.ok:
                            continue
                        data = None
                        try:
                            data = r_sem.json()
                        except ValueError:
                            import json
                            try:
                                data = json.loads(r_sem.text)
                            except Exception:
                                data = None
                        sem_display = sem_name or f"semester_{sem_val}"
                        if isinstance(data, list) and len(data) >= 2 and isinstance(data[1], dict):
                            info = data[1]
                            if info.get("semester_name"):
                                sem_display = str(info.get("semester_name"))
                            if info.get("scr") is not None:
                                result[f"{sem_display} - SCR"] = str(info.get("scr"))
                            if info.get("sgp") is not None:
                                result[f"{sem_display} - SGP"] = str(info.get("sgp"))
                            if info.get("sgpa") is not None:
                                result[f"{sem_display} - SGPA"] = str(info.get("sgpa"))
                            if info.get("cgpa") is not None:
                                result[f"{sem_display} - CGPA"] = str(info.get("cgpa"))
                            if info.get("semester_status") is not None:
                                result[f"{sem_display} - Status"] = str(info.get("semester_status"))
                        elif r_sem.text:
                            txt = r_sem.text
                            def find_num3(key: str) -> Optional[str]:
                                m = re.search(rf"\b{key}\b" + r"\s*[:=]\s*\"?([0-9.]+)\"?", txt, re.I)
                                return m.group(1) if m else None
                            sgpa_v = find_num3("sgpa")
                            cgpa_v = find_num3("cgpa")
                            sgp_v = find_num3("sgp")
                            scr_v = find_num3("scr")
                            status_m = re.search(r"semester_status\s*[:=]\s*\"?([^\"\n]+)\"?", txt, re.I)
                            if sgpa_v:
                                result[f"{sem_display} - SGPA"] = sgpa_v
                            if cgpa_v:
                                result[f"{sem_display} - CGPA"] = cgpa_v
                            if sgp_v:
                                result[f"{sem_display} - SGP"] = sgp_v
                            if scr_v:
                                result[f"{sem_display} - SCR"] = scr_v
                            if status_m:
                                result[f"{sem_display} - Status"] = status_m.group(1).strip()
                    except Exception:
                        continue

        if not result:
            raise ValueError("Could not parse points. Update selectors in portal.py:fetch_points()")

        return result
