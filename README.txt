WINDOWS CMD / YEREL GEMMA / JIRA SERVER 8.16.2 AGENT
=================================================

HEDEF ORTAM
-----------
Windows 10/11 x64, Python 3.11 x64 başlangıç hedefidir.
Runtime: llama-cpp-python==0.3.35, Windows amd64 Vulkan wheel.
Model: gemma 4 12b agentic fable5 composer2.5 v2 3.5x tau2 gguf
(kullanıcının belirttiği model; gerçek dosya/backend uyumu hedefte doğrulanır).
Model ve wheel dosyaları bu repository ile dağıtılmaz.

Kurulum, CLI, testler ve yardımcı araçlar PowerShell kullanmaz.
LLM çıkarımı yereldir; Jira araçları yalnız yapılandırılmış kapalı ağ Jira sunucusuna
erişir. İnternet servisi, Cloud Jira veya yeni runtime bağımlılığı kullanılmaz.

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
    GEMMA_SHOW_THOUGHTS=1, GEMMA_VERBOSE_LLAMA=0
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
    py/py.exe PATH üzerinde kurulu Python launcher gerektirir; -3 gibi sürüm
    seçimleri korunur ve aktif ajandan farklı bir yorumlayıcı seçebilir.
    Python ve py çağrılarında UTF-8 ortamı ve standart giriş/çıkış kodlaması
    ayarlanır. run_python aynı UTF-8 ayarlarını kullanır.
    Kabuk adı kontrolü program adına uygulanır; "cmd" metni veya cmd.py gibi
    Python dosya adları bu kontrol nedeniyle reddedilmez.
    Diğer programlar PATH üzerindeki workspace dışı açık .exe dosyalarına
    çözümlenir; workspace'teki aynı isimli betik/program otomatik seçilmez.
    GEMMA_COMMAND_ALLOWLIST ile yönetici program listesini değiştirebilir;
    shell ve batch yasağı bu ayarla açılamaz.
    stderr uyarıları tek başına başarısızlık değildir; çıkış kodu belirleyicidir.
    Komutlar arka planda çalışır; etkileşimli terminal penceresi açılmaz.
    Girdi stdin ile önceden verilir. Hedef süreç başlatılamazsa stderr içinde
    process.target.spawn, exception türü, errno ve winerror bulunur; ham hata
    metni ve argümanlar bu tanı mesajına eklenmez. Çıkış kodu 126 korunur.

Jira Server 8.16.2 /rest/api/2:
    jira_search(jql, start_at=0, max_results=20)
    jira_get_issue(issue_key)
    jira_get_comments(issue_key, start_at=0, max_results=20)
    jira_list_projects(start_at=0, max_results=20)
    jira_get_create_metadata(project_key, issue_type_id?, start_at=0,
                             max_results=20, field_id?)
    jira_create_issue(project_key, issue_type_id, summary, description?, fields?)
    jira_create_subtask(parent_key, issue_type_id, summary, description?, fields?)

    API v2 ve Server JSON biçimi kullanılır; Cloud ADF/accountId kullanılmaz.
    HTTPS adresinde /jira gibi context path desteklenir:
        JIRA_BASE_URL=https://jira.example.com/jira
    Her araç çağrısı /myself ile oturumu doğrular. Alt istekler aynı ayar ve
    oturum görüntüsünü kullanır; sonraki araç çağrısı .env'yi yeniden okur.
    Yönlendirmeler takip edilmez. Cookie modele ve araç argümanlarına girmez.
    TLS doğrulaması kapatılamaz; gerekirse JIRA_CA_FILE kullanılır.

    Proje listesi /project yanıtından yerel sayfalanır. Metadata
    /issue/createmeta üzerinden okunur. Önce proje iş türleri, sonra türün
    alanları istenir. Tür adları ve ID'leri sabit kabul edilmez.
    Her alanın ilk 10 seçeneği gösterilir; allowed_values_total daha büyükse
    field_id ile seçenekler sayfalanır. Sayfa üst sınırı 50'dir.
    has_more ve next_start_at sonraki sayfayı belirtir. Yanıt 2 MiB'ı aşarsa
    açık hata verilir. Büyük sunucu yanıtları sessizce eksik sayılmaz.

    Oluşturmadan önce create-screen alanları ve zorunluluklar doğrulanır.
    fields ek alanlar nesnesidir; project/issuetype/parent/summary/description
    burada tekrar verilemez. Seçenekler {"id":"..."}, kullanıcı/grup alanları
    {"name":"..."}, çoklu alanlar dizi, açıklama düz metin biçimindedir.
    Özel alanlar gerçek customfield_* kimlikleriyle gönderilir. Tarih ISO
    YYYY-MM-DD, datetime saat dilimi içeren ISO metnidir.
    Zorunlu alan eksikse ajan bilgiyi kullanıcıdan ister; değer uydurmaz.
    Varsayılanı bulunan alan gönderilmeyebilir. Kurumsal eklenti alanları ve
    cascading select için özel adaptör gerekir; format tahmin edilmez.
    Subtask'ın projesi üst işten alınır; subtask altına subtask açılamaz.
    Oluşturma sonucu key, id, url içerir. Okumada üst iş, alt iş özetleri,
    proje, tür ve öncelik de döner. İlk 50 alt iş gösterilir; daha fazlası
    varsa subtasks_truncated=true olur, devamı parent = KEY JQL ile okunur.

    Kullanıcı açıkça task/subtask oluşturulmasını istediğinde, bilgiler
    tamamsa ayrıca onay sorulmaz. Belirsiz proje/üst iş/alanlar sorulur.
    GET istekleri geçici hatalarda en fazla bir kez tekrarlanır. Oluşturma
    POST'u otomatik tekrarlanmaz. Yanıt kaybolursa outcome_unknown döner;
    ajan JQL/okuma ile kontrol etmeden tekrar oluşturmamalıdır. Aynı turdaki
    aynı başarılı veya belirsiz oluşturma çağrısı yeniden gönderilmez.
    Bu kontrol sunucu idempotency garantisi değildir; yeniden başlatma veya
    farklı argümanlarla çağrı aynı korumaya girmez.
    Socket timeout 30 saniyedir. Yetki, oturum, TLS, doğrulama ve belirsiz
    oluşturma sonuçları ayrıdır. Sunucunun alan hataları maskelenir.
    Güncelleme, silme, transition ve ek yükleme kapsam dışıdır.

Örnek konuşma:
    Kullanıcı: Erişebildiğim Jira projelerini göster.
    Kullanıcı: APP projesinde "Offline raporlama" başlıklı task aç.
    Ajan: [Metadata zorunlu Team alanını gösteriyorsa] Hangi takım?
    Kullanıcı: Platform.
    Ajan: [Araç başarısından sonra] APP-123 oluşturuldu: .../browse/APP-123
    Kullanıcı: Bunun altına "Birim testlerini ekle" subtask'ını aç.
    Kullanıcı: project = APP AND statusCategory != Done sorgusunu çalıştır.


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
Terminal aynı süreçte konuşma geçmişini bellekte tutar; /reset geçmişi temizler.
Geçmiş diske yazılmaz ve yeniden başlatmada kaybolur. Eski konuşma turları
bütçe aşımında bütün olarak çıkarılır; araç çağrısı/sonuç çiftleri korunur.
İşlemden sonra model hatası oluşursa eldeki araç sonuçları sonraki mesaja
aktarılır. Referans artık bağlamda değilse ajan kullanıcıya sorar.
Programatik run() bağımsız kalır; chat() konuşmayı sürdürür ve
reset_conversation() bellekteki konuşmayı temizler.

Terminal varsayılan olarak geliştirici odaklı DEBUG ayrıntılarını gösterir:
    - Zaman damgası, adım, gerçek araç adı ve çağrı kimliği.
    - Komut, parametreler, stdin, çalışma dizini ve gönderilen timeout.
      Açıklama verilse bile gerçek komut ayrıca görünür.
    - Araç sonucu alanları, exit_code, timed_out, error/error_code ve süre.
      stdout, stderr ve dosya içeriği ayrı, çok satırlı bloklarda gösterilir.
    - Model yükleme/çıkarım, bağlam hazırlama ve araç çalıştırma aşamaları;
      exception türü, mesajı ve dosya:satır/fonksiyon konumları.
    - Geçersiz model yanıtının nedeni, hatalı araç/parametre, tekrar sayısı,
      finish_reason, varsa token kullanımı ve bağlamdan çıkarılan mesaj sayısı.
Araçlara yalnızca modelin gönderdiği parametreler loglanır; varsayılan veya
uygulanan değerler sonuçta varsa orada görünür. Log bloğu 20.000 karakteri
aşarsa başlangıç ve son korunur, atlanan karakter sayısı açıkça belirtilir.
Alt süreç ve dosya araçlarının mevcut çıktı limitleri ayrıca geçerlidir.
Modelin thought blokları varsayılan görünür; GEMMA_SHOW_THOUGHTS=0 bunları
kapatır. Düşünceler model yanıtı tamamlandıktan sonra gösterilir.
Bilinen oturum değerleri, kimlik doğrulama başlıkları ve yaygın parola/token
alanları maskelenir; terminal kontrol karakterleri kaçış biçiminde gösterilir.
Kaynak kod satırları, yerel değişkenler ve tüm ortam değişkenleri hata stack'ine
eklenmez. Maskeleme serbest metindeki tüm hassas verileri tanıma garantisi
vermez; dosya/Jira içeriği içeren debug çıktısını paylaşmadan önce inceleyin.
Log dosyası oluşturulmaz. Jira/dosya çıktıları güvenilmeyen veri olarak kalır.

GÜVENLİK SINIRI
--------------
JIRA_JSESSIONID/JSESSIONID alt süreç ortamına aktarılmaz. Bununla birlikte
aynı Windows hesabıyla çalışan Python veya izinli programlar .env dosyasını
doğrudan okuyabilir ve başka süreçler başlatabilir. Bu uygulama tam bir OS
sandbox'ı veya Python kodu güvenlik doğrulayıcısı değildir.
PowerShell ve ağ için nihai sınır mevcut kurumsal politikalardır. Politika
değiştirme, yetki yükseltme veya engelleri aşma mekanizması sağlanmaz.
Jira araçları, aynı hesaba ait keyfi Python koduna ağ izolasyonu sağlamaz.
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
4. Terminalden projeler, JQL, iş/subtask detayı ve yorum okuma çalışmalı.
   Test için ayrılmış projede normal task ve ardından subtask oluşturulmalı;
   anahtarlar, alanlar ve üst iş bağlantısı Jira ekranında kontrol edilmeli.
   Kuruma özel zorunlu alanla çok mesajlı konuşma ve /reset denenmeli.
   Gerçek Jira 8.16.2 uyumluluğu bu hedef kabulüyle doğrulanır; mock testler
   kurumsal eklenti, SSO veya create-screen yapılandırmasını kanıtlamaz.
5. .env oturumu yenilenince bir sonraki Jira isteği yeni değeri kullanmalı.
6. Kurumsal ekip PowerShell/ağ politikasının alt süreçlere de uygulandığını
   doğrulamalı; testler PowerShell'i gerçekten başlatmayı denemez.

Statik kontroller (Ruff geliştirme aracı; runtime bağımlılığı değildir):
    ruff check .
    ruff format --check agent.py terminal_ui.py agent_tools.py config.py settings.py jira_client.py jira_fields.py test_jira_write.py test_jira_fields.py test_jira_conversation.py process_runner.py process_worker.py windows_job.py runtime_check.py test_agent.py test_agent_loop.py test_terminal_ui.py test_jira.py test_settings.py test_process_runner.py test_tools.py test_runtime_check.py

MİMARİ
------
agent.py: CLI, model kurulumu, budget ve tool döngüsü.
terminal_ui.py: adım, düşünce, maskelenmiş debug çıktısı ve hata aşaması sunumu.
agent_tools.py: dosya/komut araçları, şemalar ve dispatcher.
gemma_parser.py: mevcut JSON/Gemma ayrıştırıcısı.
settings.py / config.py: literal .env, maskeleme ve doğrulanmış ayarlar.
jira_client.py: cookie tabanlı Jira Server REST v2 istemcisi ve araç şemaları.
jira_fields.py: create metadata üzerinden alan değeri doğrulaması.
process_runner.py / process_worker.py / windows_job.py: pipe tamponları,
başlangıç kapısı ve Windows süreç ağacı yaşam döngüsü.
runtime_check.py: offline wheel envanteri ve hedef kabul kontrolleri.

REFERANSLAR
-----------
https://developer.atlassian.com/server/jira/platform/cookie-based-authentication/
https://developer.atlassian.com/server/jira/platform/rest/v10000/api-group-myself/
https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/cmd
