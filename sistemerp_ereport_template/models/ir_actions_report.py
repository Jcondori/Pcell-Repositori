# -*- coding: utf-8 -*-

import io
import os
import base64
import tempfile
import logging
import subprocess
from contextlib import closing
from odoo.exceptions import UserError
from PyPDF2 import PdfFileWriter, PdfFileReader
from odoo.tools.misc import find_in_path
from odoo import api, models, tools, _


_logger = logging.getLogger(__name__)

def _get_wkhtmltopdf_bin():
    return find_in_path('wkhtmltopdf')


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def merge_extra_content_pdf(self, res_ids, pdf_content_stream):
        if not res_ids:
            return pdf_content_stream

        content_obj = self.env['report.extra.content']
        model_id = self.env['ir.model'].sudo().search([('model', '=', self.model)])
        res_id = self.env[self.model].browse(res_ids)
        extra_content_id = content_obj.search([('model_id', '=', model_id.id), ('company_id', '=', res_id.company_id.id)])
        custom_documents = []
        if extra_content_id and extra_content_id.append_extra_content:
            main_pdf_fd, main_pdf_path = tempfile.mkstemp(suffix='.pdf', prefix='report.tmp.custom.')
            custom_documents.append(main_pdf_path)
            with open(main_pdf_fd, 'wb') as pcd:
                pcd.write(pdf_content_stream.read())
        
            pdf_custom_fd, pdf_custom_path = tempfile.mkstemp(suffix='.pdf', prefix='report.tmp.custom.')
            custom_documents.append(pdf_custom_path)
            with open(pdf_custom_fd, 'wb') as pcd:
                pcd.write(base64.decodestring(extra_content_id.append_extra_content))
        
        if custom_documents:
            writer = PdfFileWriter()
            streams = []  # We have to close the streams *after* PdfFilWriter's call to write()
            merged_file_fd, merged_file_path = tempfile.mkstemp(suffix='.pdf', prefix='report.merged.tmp.')
            try:
                for document in custom_documents:
                    pdfreport = open(document, 'rb')
                    streams.append(pdfreport)
                    reader = PdfFileReader(pdfreport)
                    for page in range(0, reader.getNumPages()):
                        writer.addPage(reader.getPage(page))

                with closing(os.fdopen(merged_file_fd, 'wb')) as merged_file:
                    writer.write(merged_file)
            finally:
                for stream in streams:
                    try:
                        stream.close()
                    except Exception:
                        pass
            custom_documents.append(merged_file_path)

            with open(merged_file_path, 'rb') as pdf_document:
                pdf_content_stream = pdf_document.read()
            pdf_content_stream = io.BytesIO(pdf_content_stream)

            for temporary_file in custom_documents:
                try:
                    os.unlink(temporary_file)
                except (OSError, IOError):
                    _logger.error('Error when trying to remove file %s' % temporary_file)

        return pdf_content_stream

    def _post_pdf(self, save_in_attachment, pdf_content=None, res_ids=None):
        '''Merge the existing attachments by adding one by one the content of the attachments
        and then, we add the pdf_content if exists. Create the attachments for each record individually
        if required.

        :param save_in_attachment: The retrieved attachments as map record.id -> attachment_id.
        :param pdf_content: The pdf content newly generated by wkhtmltopdf.
        :param res_ids: the ids of record to allow postprocessing.
        :return: The pdf content of the merged pdf.
        '''
        def close_streams(streams):
            for stream in streams:
                try:
                    stream.close()
                except Exception:
                    pass

        # Check special case having only one record with existing attachment.
        if len(save_in_attachment) == 1 and not pdf_content:
            return base64.decodestring(list(save_in_attachment.values())[0].datas)
        
        # Create a list of streams representing all sub-reports part of the final result
        # in order to append the existing attachments and the potentially modified sub-reports
        # by the postprocess_pdf_report calls.
        streams = []

        # In wkhtmltopdf has been called, we need to split the pdf in order to call the postprocess method.
        if pdf_content:
            pdf_content_stream = io.BytesIO(pdf_content)
            # Build a record_map mapping id -> record
            record_map = {r.id: r for r in self.env[self.model].browse([res_id for res_id in res_ids if res_id])}

            # If no value in attachment or no record specified, only append the whole pdf.
            content_obj = self.env['report.extra.content']
            model_id = self.env['ir.model'].sudo().search([('model', '=', self.model)])
            custom_documents = []
            
            if not record_map or not self.attachment:
                if res_ids:
                    main_pdf_fd, main_pdf_path = tempfile.mkstemp(suffix='.pdf', prefix='report.tmp.custom.')
                    custom_documents.append(main_pdf_path)
                    with open(main_pdf_fd, 'wb') as pcd:
                        pcd.write(pdf_content_stream.read())

                    for res in self.env[self.model].browse(res_ids):
                        extra_content_id = content_obj.search([('model_id', '=', model_id.id), ('company_id', '=', res.company_id.id)])
                        if extra_content_id and extra_content_id.append_extra_content:
                            pdf_custom_fd, pdf_custom_path = tempfile.mkstemp(suffix='.pdf', prefix='report.tmp.custom.')
                            custom_documents.append(pdf_custom_path)
                            with open(pdf_custom_fd, 'wb') as pcd:
                                pcd.write(base64.decodestring(extra_content_id.append_extra_content))
                    
                    if custom_documents:
                        writer = PdfFileWriter()
                        st = []  # We have to close the streams *after* PdfFilWriter's call to write()
                        merged_file_fd, merged_file_path = tempfile.mkstemp(suffix='.pdf', prefix='report.merged.tmp.')
                        try:
                            for document in custom_documents:
                                pdfreport = open(document, 'rb')
                                st.append(pdfreport)
                                reader = PdfFileReader(pdfreport)
                                for page in range(0, reader.getNumPages()):
                                    writer.addPage(reader.getPage(page))

                            with closing(os.fdopen(merged_file_fd, 'wb')) as merged_file:
                                writer.write(merged_file)
                        finally:
                            for stt in st:
                                try:
                                    stt.close()
                                except Exception:
                                    pass
                        custom_documents.append(merged_file_path)
                        with open(merged_file_path, 'rb') as pdf_document:
                            pdf_content_stream = io.BytesIO(pdf_document.read())
                streams.append(pdf_content_stream)
            else:
                if len(res_ids) == 1:
                    # Only one record, so postprocess directly and append the whole pdf.
                    pdf_content_stream = self.merge_extra_content_pdf(res_ids[0], pdf_content_stream)
                    if res_ids[0] in record_map and not res_ids[0] in save_in_attachment:
                        self.postprocess_pdf_report(record_map[res_ids[0]], pdf_content_stream)
                    streams.append(pdf_content_stream)
                else:
                    # In case of multiple docs, we need to split the pdf according the records.
                    # To do so, we split the pdf based on outlines computed by wkhtmltopdf.
                    # An outline is a <h?> html tag found on the document. To retrieve this table,
                    # we look on the pdf structure using pypdf to compute the outlines_pages that is
                    # an array like [0, 3, 5] that means a new document start at page 0, 3 and 5.
                    reader = PdfFileReader(pdf_content_stream)
                    outlines_pages = sorted(
                        [outline.getObject()[0] for outline in reader.trailer['/Root']['/Dests'].values()])
                    assert len(outlines_pages) == len(res_ids)
                    for i, num in enumerate(outlines_pages):
                        to = outlines_pages[i + 1] if i + 1 < len(outlines_pages) else reader.numPages
                        attachment_writer = PdfFileWriter()
                        for j in range(num, to):
                            attachment_writer.addPage(reader.getPage(j))
                        
                        # Append Extra content at last page of pdf report
                        if model_id and res_ids[i]:
                            res_id = self.env[self.model].browse(res_ids[i])
                            extra_content_id = content_obj.search([('model_id', '=', model_id.id), ('company_id', '=', res_id.company_id.id)])
                            custom_documents = []
                            if extra_content_id and extra_content_id.append_extra_content:
                                st = []
                                pdf_custom_fd, pdf_custom_path = tempfile.mkstemp(suffix='.pdf', prefix='report.tmp.custom.')
                                custom_documents.append(pdf_custom_path)
                                with open(pdf_custom_fd, 'wb') as pcd:
                                    pcd.write(base64.decodestring(extra_content_id.append_extra_content))

                                ext_pdfreport = open(pdf_custom_path, 'rb')
                                st.append(ext_pdfreport)
                                ext_reader = PdfFileReader(ext_pdfreport)
                                for page in range(0, ext_reader.getNumPages()):
                                    attachment_writer.addPage(ext_reader.getPage(page))
                        
                        stream = io.BytesIO()
                        attachment_writer.write(stream)
                        if res_ids[i] and res_ids[i] not in save_in_attachment:
                            self.postprocess_pdf_report(record_map[res_ids[i]], stream)
                        streams.append(stream)
                    close_streams([pdf_content_stream])
            for temporary_file in custom_documents:
                try:
                    os.unlink(temporary_file)
                except (OSError, IOError):
                    _logger.error('Error when trying to remove file %s' % temporary_file)

        # If attachment_use is checked, the records already having an existing attachment
        # are not been rendered by wkhtmltopdf. So, create a new stream for each of them.
        if self.attachment_use:
            for attachment_id in save_in_attachment.values():
                content = base64.decodestring(attachment_id.datas)
                streams.append(io.BytesIO(content))

        # Build the final pdf.
        writer = PdfFileWriter()
        for stream in streams:
            reader = PdfFileReader(stream)
            writer.appendPagesFromReader(reader)
        result_stream = io.BytesIO()
        streams.append(result_stream)
        writer.write(result_stream)
        result = result_stream.getvalue()

        # We have to close the streams after PdfFileWriter's call to write()
        close_streams(streams)
        return result