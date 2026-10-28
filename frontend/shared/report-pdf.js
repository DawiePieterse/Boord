// The Share / PDF button on a print page (printPage in admin/app.js).
//
// On a phone the print dialog is the wrong door: the office wants the report
// in an email or a WhatsApp to the agent. So the page makes itself into a
// landscape A4 PDF as soon as it opens, and the button hands that file to the
// phone's own share sheet, which offers whatever the phone can do with it:
// mail, messages, WhatsApp, Drive, print. A device that can't share files
// (most desktops) downloads the PDF instead.
//
// The PDF is made up front, not on the tap, because a phone only allows the
// share sheet straight after a tap: building a PDF first can take long enough
// that iOS refuses it. jsPDF and html2canvas are the same files Boord Owner
// uses for its PDFs, loaded only by print pages, never by the app itself.
(function () {
  const btn = document.getElementById("sharePdf");
  const report = document.getElementById("report");
  if (!btn || !report) return;
  let file = null;

  async function build() {
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF({ orientation: "landscape", unit: "pt", format: "a4" });
    const margin = 24;
    await new Promise((resolve) => doc.html(report, {
      callback: resolve,
      margin: [margin, margin, margin, margin],
      autoPaging: "text",
      width: doc.internal.pageSize.getWidth() - 2 * margin,
      // Laid out as on a desktop screen, not squeezed to the phone's width.
      windowWidth: 1100,
    }));
    const name = (document.title.replace(/[^A-Za-z0-9 _-]+/g, "").trim() || "Boord report") + ".pdf";
    file = new File([doc.output("blob")], name, { type: "application/pdf" });
  }

  function download() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(file);
    a.download = file.name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  }

  btn.addEventListener("click", async () => {
    if (!file) return;
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: document.title });
      } catch (e) {
        // Closing the share sheet without picking anything lands here too.
        if (e.name !== "AbortError") download();
      }
    } else {
      download();
    }
  });

  build().then(() => {
    btn.disabled = false;
    btn.textContent = "Share / PDF";
  }).catch((e) => {
    console.error("PDF failed:", e);
    btn.textContent = "PDF failed - use Print";
  });
})();
