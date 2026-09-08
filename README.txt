WINDOWS CMD / YEREL GEMMA / SALT OKUNUR JIRA AGENT
=================================================

HEDEF ORTAM
-----------
Windows 10/11 x64, Python 3.11 x64 başlangıç hedefidir.
Runtime: llama-cpp-python==0.3.35, Windows amd64 Vulkan wheel.
Model: gemma 4 12b agentic fable5 composer2.5 v2 3.5x tau2 gguf
(kullanıcının belirttiği model; gerçek dosya/backend uyumu hedefte doğrulanır).
Model ve wheel dosyaları bu repository ile dağıtılmaz.

Kurulum, CLI, testler ve yardımcı araçlar PowerShell kullanmaz.
LLM çıkarımı yereldir; Jira araçları ağ erişimi gerektirir.

CMD İLE ÇEVRİMDIŞI KURULUM
-------------------------
Komutları proje kökünde ayrı satırlarda Windows CMD üzerinden çalıştırın:

    py -3.11 -m venv .venv
    .venv\Scripts\activate
    python runtime_check.py --wheels-only

wheels/ altında hedef Python ABI'sine uygun 0.3.35 Vulkan wheel ve tüm
bağımlılık wheel'leri bulunmalıdır. Kontrol her wheel'in SHA-256 değerini
ve runtime ABI uyumunu bildirir; çıktıyı dağıtım envanteri olarak saklayın.
Hash listesi kimlik/imza doğrulaması değildir; wheel kaynağı kurumca onaylanmalıdır.
Kontrol başarısızsa doğru wheel'i temin edin; başka sürüme otomatik geçilmez.

    python -m pip install --no-index --find-links=.\wheels -r requirements.txt
    if not exist .env copy .env.example .env

Gerekirse kurumun izin verdiği bir metin düzenleyiciyle .env dosyasını düzenleyin.
GGUF dosyasını model/model.gguf konumuna koyun veya GEMMA_MODEL_PATH belirleyin.

    python runtime_check.py --load-model
    python -m unittest discover -v
    python test_agent.py
    python agent.py

runtime_check sürümü, 64-bit Windows'u ve GPU offload kabiliyetini kontrol eder;
backend bilgisinin Vulkan gösterdiğini ve modelin GPU'da yüklendiğini hedefte
doğrulayın. Başka GPU backend'i Vulkan olarak kabul edilmez. Vulkan sürücüsü
ve model belleği kurumun sağladığı makinede doğrulanmalıdır.
CPU bilerek kullanılacaksa GEMMA_N_GPU_LAYERS=0 yapılır; GPU hatasında sessiz
CPU fallback uygulanmaz. Runtime kontrolünün GPU kabul koşulu yine başarısızdır.

YAPILANDIRMA VE OTURUM
---------------------
.env yalnızca uygulama tarafından okunur; otomatik oluşturulmaz, silinmez,
üzerine yazılmaz veya os.environ içine topluca aktarılmaz. Gerçek ortam
değişkenleri .env değerlerinden önceliklidir. Boş ortam değeri de önceliklidir.
Yalnız KEY=VALUE, tam satır # yorumları ve eşleşen tek/çift tırnak desteklenir.
Shell, değişken genişletme ve inline yorum çalıştırılmaz.
.env, .env.* (örnek hariç), model/, wheels/, workspace/ ve cache'ler Git dışıdır.

Jira ayarları:
    JIRA_BASE_URL=https://jira.example.com
    JIRA_JSESSIONID=
    JIRA_CA_FILE=

Gerçek JSESSIONID değerini yalnız yerel .env dosyasına veya başlatan ortama
yerleştirin; kaynak koda, testlere, issue açıklamalarına veya sohbet girişine
yazmayın. Tam Cookie başlığı değil, yalnız oturum değeri beklenir.
JIRA_CA_FILE isteğe bağlı kurumsal CA bundle yoludur; TLS kapatılamaz.
Proxy varsa Python urllib standart ortam/sistem proxy ayarlarını kullanır.
Proje .env dosyası genel proxy ayarlarını alt süreçlere veya os.environ'a yüklemez.

Her Jira araç çağrısı .env'yi yeniden okur; yenilenen oturum bir sonraki çağrıda
kullanılır. Ortam değişkeniyle verilen oturum için uygulamayı yeniden başlatın.
Dosyanın kalıcı olması oturumu süresiz yapmaz. Oturum sona erince kullanıcı
değeri günceller; otomatik login, parola isteme veya tarayıcı cookie okuma yoktur.

    python runtime_check.py --jira

Bu komut gerçek Jira oturumunu okuma isteğiyle kontrol eder, değeri yazdırmaz.

Diğer varsayılanlar:
    GEMMA_N_CTX=16384, GEMMA_MAX_TOKENS=2048, GEMMA_TEMPERATURE=0.2
    GEMMA_N_GPU_LAYERS=-1, GEMMA_N_THREADS=0 (otomatik)
    GEMMA_MAX_AGENT_STEPS=15, GEMMA_MAX_UNKNOWN_TOOL_ATTEMPTS=3
    GEMMA_SHOW_THOUGHTS=0, GEMMA_VERBOSE_LLAMA=0
    GEMMA_COMMAND_TIMEOUT_SECONDS=30
    GEMMA_COMMAND_MAX_OUTPUT_CHARS=20000, GEMMA_COMMAND_MAX_LENGTH=4000
    GEMMA_FILE_MAX_BYTES=1048576
    GEMMA_AGENT_WORKSPACE=workspace konumunun varsayılan mutlak yolu

ENV içindeki göreli model yolları başlatma dizinine göredir; proje kökünden
başlatın veya mutlak model yolu verin. Workspace uygulama kökünü içeremez.

ARAÇLAR
-------
list_files(directory=".") / read_file(path) / write_file(path, content):
    Workspace içindeki UTF-8 dosyaları yönetir. Dosya boyutu varsayılan 1 MiB.
    .env adları, mutlak yollar ve çözümlenen workspace kaçışları reddedilir.
    write_file mevcut içeriğin üzerine yazar; bir yedekleme sistemi değildir.

run_python(script, arguments=[], working_directory=".", stdin, timeout_seconds):
    Aktif Python yorumlayıcısıyla .py dosyası çalıştırır; shell kullanılmaz.
    İnteraktif test girdisini stdin üzerinden iletir. UTF-8 kullanır.

run_command(command, working_directory=".", stdin, timeout_seconds, description):
    Windows API ile bulunan sistem cmd.exe /d /s /c üzerinden tek komut.
    COMSPEC'e güvenmez. PowerShell, pwsh, iç içe shell, .bat/.cmd, pipe,
    yönlendirme, komut zinciri, environment expansion ve açık yol kaçışları
    engellenir. GEMMA_ALLOW_CMD_OPERATORS=1 artık başlangıç hatasıdır.
    Varsayılan programlar:
    python, py, pytest, ruff, mypy, git, dir, type, where, tree (+ .exe biçimleri).
    dir/type CMD builtin olarak çalışır. Python aktif yorumlayıcıya bağlanır.
    Diğer programlar PATH üzerindeki workspace dışı açık .exe dosyalarına
    çözümlenir; workspace'teki aynı isimli betik/program otomatik seçilmez.
    GEMMA_COMMAND_ALLOWLIST ile yönetici program listesini değiştirebilir;
    shell ve batch yasağı bu ayarla açılamaz.
    stderr uyarıları tek başına başarısızlık değildir; çıkış kodu belirleyicidir.

Jira Server/Data Center /rest/api/2:
    jira_search(jql, start_at=0, max_results=20)
    jira_get_issue(issue_key)
    jira_get_comments(issue_key, start_at=0, max_results=20)

    Yalnız yapılandırılmış HTTPS origin'i, GET ve tanımlı REST uçları kullanılır.
    Her işlem önce /myself ile oturumun kimliğini doğrular; herkese açık Jira
    verisi yanlışlıkla geçerli oturum olarak kabul edilmez. Ardından iş okunur.
    Genel URL veya header argümanı yoktur; cookie modele verilmez.
    Sayfa üst sınırı 50; has_more ve next_start_at ile sayfalama yapılır.
    İş detayı ve yorumların uzun metinleri açık bir işaretle kısaltılır.
    İstek socket timeout'u 30 saniye; yanıt üst sınırı 2 MiB.
    Ağ/geçici sunucu hataları en fazla bir kez tekrar edilir. Yönlendirme
    takip edilmez. Yetki, bulunamayan iş, bozuk JQL, TLS ve oturum hataları
    ayrılır. Jira'ya yazma, ek indirme ve tarayıcı otomasyonu bulunmaz.
    URL context path içermez; hedef Jira bu origin'in kökünde varsayılır.

SÜREÇ VE AGENT DAVRANIŞI
-----------------------
Windows'ta hedef komut, izole Python başlangıç aracının Job Object'e başarıyla
bağlanmasından sonra serbest bırakılır. Bağlanma başarısızsa hedef çalışmaz.
Job kapatıldığında alt süreçler de sonlanır; komut bittikten sonra arka planda
kalıcı servis bırakılmaz. Ctrl+C ve timeout aynı temizlik yolunu kullanır.
stdout/stderr çalışırken sınırlı tamponlarla boşaltılır.

Structured/native Gemma çağrıları ortak doğrulama yolundadır. Üç ardışık
geçersiz/eksik/bilinmeyen yanıt durdurulur; geçerli çağrı sayacı sıfırlar.
Boş veya token sınırında kesilmiş çıktı tamamlanmış görev sayılmaz.
Bağlam bütçesi model tokenizer'ıyla şema+mesajlar üzerinden tahmin edilir,
chat template için pay ve yanıt alanı ayrılır. Eski tamamlanmış çağrı/sonuç
grupları birlikte çıkarılır; ilk görev ve en son grup korunur. Yine sığmazsa
görev küçültme hatası döner. Gerçek template hesabının son otoritesi llama.cpp'dir.
Her yeni terminal isteği önceki run geçmişinden bağımsızdır.

Araç logları ad ve durum özetidir. Oturum değerleri ve kimlik doğrulama
başlıkları maskelenir. Thought gösterimi açıkça etkinleştirilebilir fakat
varsayılan kapalıdır. Jira/dosya çıktıları talimat değil güvenilmeyen veridir.

GÜVENLİK SINIRI
--------------
JIRA_JSESSIONID/JSESSIONID alt süreç ortamına aktarılmaz. Bununla birlikte
aynı Windows hesabıyla çalışan Python veya izinli programlar .env dosyasını
doğrudan okuyabilir ve başka süreçler başlatabilir. Bu uygulama tam bir OS
sandbox'ı veya Python kodu güvenlik doğrulayıcısı değildir.
PowerShell ve ağ için nihai sınır mevcut kurumsal politikalardır. Politika
değiştirme, yetki yükseltme veya engelleri aşma mekanizması sağlanmaz.
Salt okunur Jira aracı, aynı hesaba ait keyfi Python koduna ağ izolasyonu sağlamaz.
Kurulum dizini ve .env ACL'leri kurum politikasına uygun tutulmalıdır.

TESTLER VE HEDEFTE KABUL
-----------------------
python -m unittest discover -v:
    Model ve gerçek Jira gerektirmez. Windows'a özel testler macOS/Linux'ta
    gerekçeli atlanır. Testlerde yalnız sentetik oturum örnekleri kullanılır.

python test_agent.py:
    Gerçek GGUF ile geçici workspace'te dosya oluşturma, okuma, Python
    çalıştırma ve Türkçe çıktıyı assertion ile kontrol eder. Mevcut workspace
    dosyalarını değiştirmez. Başarısız assertion testi başarısız kılar.

python runtime_check.py --load-model --jira:
    Hedef makinede runtime, model ve gerçek Jira oturumunu doğrular.

Windows kabul:
1. Model/wheel ABI, Vulkan backend ve bellek kontrolü geçmeli.
2. Unit testlerde Windows CMD ve Job Object testleri atlanmadan geçmeli.
3. test_agent.py başarılı olmalı.
4. Terminalden izinli bir JQL araması, iş detayı ve yorum okuma çalışmalı.
5. .env oturumu yenilenince bir sonraki Jira isteği yeni değeri kullanmalı.
6. Kurumsal ekip PowerShell/ağ politikasının alt süreçlere de uygulandığını
   doğrulamalı; testler PowerShell'i gerçekten başlatmayı denemez.

Statik kontroller (Ruff geliştirme aracı; runtime bağımlılığı değildir):
    ruff check .
    ruff format --check agent.py agent_tools.py config.py settings.py jira_client.py process_runner.py process_worker.py windows_job.py runtime_check.py test_agent.py test_agent_loop.py test_jira.py test_settings.py test_process_runner.py test_tools.py test_runtime_check.py

MİMARİ
------
agent.py: CLI, model kurulumu, budget ve tool döngüsü.
agent_tools.py: dosya/komut araçları, şemalar ve dispatcher.
gemma_parser.py: mevcut JSON/Gemma ayrıştırıcısı.
settings.py / config.py: literal .env, maskeleme ve doğrulanmış ayarlar.
jira_client.py: cookie tabanlı sınırlı salt okunur REST istemcisi.
process_runner.py / process_worker.py / windows_job.py: pipe tamponları,
başlangıç kapısı ve Windows süreç ağacı yaşam döngüsü.
runtime_check.py: offline wheel envanteri ve hedef kabul kontrolleri.

REFERANSLAR
-----------
https://developer.atlassian.com/server/jira/platform/cookie-based-authentication/
https://developer.atlassian.com/server/jira/platform/rest/v10000/api-group-myself/
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cmd
