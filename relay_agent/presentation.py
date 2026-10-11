"""Prepare Qt pages once; native Qt owns the window's shared backing store."""
from PySide6.QtGui import QColor, QPixmap


def prepare_pages(window):
    window.layout().activate()
    window.centralWidget().layout().activate()
    window.pages.setGeometry(window.page_host.rect())
    buffers=[]
    for page in window.page_widgets:
        page.ensurePolished(); page.layout().activate()
        image=QPixmap(max(1,page.width()),max(1,page.height()))
        image.fill(QColor('#14171c')); page.render(image); buffers.append(image)
    return buffers
