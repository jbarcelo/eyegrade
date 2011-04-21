import sys
import os
from optparse import OptionParser
import imageproc
import time
import copy
import csv
import re

# Local imports
import utils
import gui

# Import the cv module. If new style bindings not found, use the old ones:
try:
    import cv
    cv_new_style = True
except ImportError:
    import cvwrapper
    cv = cvwrapper.CVWrapperObject()
    cv_new_style = False

param_max_wait_time = 0.15 # seconds

# Other initializations:
regexp_id = re.compile('\{student-id\}')
regexp_seqnum = re.compile('\{seq-number\}')

class Exam(object):
    def __init__(self, image, model, solutions, valid_student_ids,
                 im_id, save_stats, score_weights):
        self.image = image
        self.model = model
        self.solutions = solutions
        self.im_id = im_id
        self.correct = None
        self.score = None
        self.original_decisions = copy.copy(self.image.decisions)
        self.save_stats = save_stats
        self.student_names = valid_student_ids
        self.student_id_filter = []
        self.student_id_manual = []
        if self.image.options['read-id']:
            self.student_id = self.decide_student_id(valid_student_ids)
        else:
            # Allow manual insertion of ID even if OCR detection
            # is not done
            self.student_id = '-1'
            if valid_student_ids is not None:
                self.ids_rank = [(0, sid) for sid in valid_student_ids]
                self.ids_rank_pos = -1
            else:
                self.ids_rank = None
        self.locked = False
        self.score_weights = score_weights

    def grade(self):
        good = 0
        bad = 0
        undet = 0
        self.correct = []
        for i in range(0, len(self.image.decisions)):
            if len(self.solutions) > 0 and self.image.decisions[i] > 0:
                if self.solutions[i] == self.image.decisions[i]:
                    good += 1
                    self.correct.append(True)
                else:
                    bad += 1
                    self.correct.append(False)
            elif self.image.decisions[i] < 0:
                undet += 1
                self.correct.append(False)
            else:
                self.correct.append(False)
        blank = self.image.num_questions - good - bad
        if self.score_weights is not None:
            score = good * self.score_weights[0] - \
                bad * self.score_weights[1] - blank * self.score_weights[2]
            max_score = self.image.num_questions * self.score_weights[0]
        else:
            score = None
            max_score = None
        self.score = (good, bad, blank, undet, score, max_score)

    def draw_answers(self):
#        good, bad, blank, undet = self.score
        self.image.draw_answers(self.locked, self.solutions, self.model,
                                self.correct, self.score[0], self.score[1],
                                self.score[3], self.im_id)

    def save_image(self, filename_pattern):
        filename = self.__saved_image_name(filename_pattern)
        cv.SaveImage(filename, self.image.image_drawn)

    def save_debug_images(self, filename_pattern):
        filename = self.__saved_image_name(filename_pattern)
        cv.SaveImage(filename + '-raw', self.image.image_raw)
        cv.SaveImage(filename + '-proc', self.image.image_proc)

    def save_answers(self, answers_file, csv_dialect, stats = None):
        f = open(answers_file, "ab")
        writer = csv.writer(f, dialect = csv_dialect)
        data = [self.im_id,
                self.student_id,
                self.model if self.model is not None else '?',
                self.score[0],
                self.score[1],
                self.score[3],
                "/".join([str(d) for d in self.image.decisions])]
        if stats is not None and self.save_stats:
            data.extend([stats['time'],
                         stats['manual-changes'],
                         stats['num-captures'],
                         stats['num-student-id-changes'],
                         stats['id-ocr-digits-total'],
                         stats['id-ocr-digits-error'],
                         stats['id-ocr-detected']])
        writer.writerow(data)
        f.close()

    def toggle_answer(self, question, answer):
        if self.image.decisions[question] == answer:
            self.image.decisions[question] = 0
        else:
            self.image.decisions[question] = answer
        self.grade()
        self.image.clean_drawn_image()
        self.draw_answers()

    def invalidate_id(self):
        self.__update_student_id(None)

    def num_manual_changes(self):
        return len([d1 for (d1, d2) in \
                        zip(self.original_decisions, self.image.decisions) \
                        if d1 != d2])

    def decide_student_id(self, valid_student_ids):
        student_id = '-1'
        self.ids_rank = None
        if self.image.id is not None:
            if valid_student_ids is not None:
                ids_rank = [(self.__id_rank(sid, self.image.id_scores), sid) \
                                for sid in valid_student_ids]
                self.ids_rank = sorted(ids_rank, reverse = True)
                student_id = self.ids_rank[0][1]
                self.image.id = student_id
                self.ids_rank_pos = 0
            else:
                student_id = self.image.id
        elif valid_student_ids is not None:
            self.ids_rank = [(0.0, sid) for sid in valid_student_ids]
        return student_id

    def try_next_student_id(self):
        if self.ids_rank is not None \
                and self.ids_rank_pos < len(self.ids_rank) - 1:
            self.ids_rank_pos += 1
            self.__update_student_id(self.ids_rank[self.ids_rank_pos][1])

    def filter_student_id(self, digit):
        if self.ids_rank is not None:
            self.student_id_filter.append(digit)
            ids = [sid for sid in self.ids_rank \
                       if ''.join(self.student_id_filter) in sid[1]]
            if len(ids) > 0:
                self.__update_student_id(ids[0][1])
            else:
                self.__update_student_id(None)
                self.student_id_filter = []
                self.ids_rank_pos = -1

    def reset_student_id_filter(self, show_first = True):
        self.student_id_filter = []
        if show_first:
            self.ids_rank_pos = 0
            self.__update_student_id(self.ids_rank[0][1])
        else:
            self.ids_rank_pos = -1
            self.__update_student_id(None)

    def student_id_editor(self, digit):
        self.student_id_manual.append(digit)
        sid = ''.join(self.student_id_manual)
        self.__update_student_id(sid)

    def reset_student_id_editor(self):
        self.student_id_manual = []
        self.__update_student_id(None)

    def lock_capture(self):
        self.locked = True

    def get_student_name(self):
        if self.student_id != '-1' and self.student_names is not None and \
                self.student_id in self.student_names:
            return self.student_names[self.student_id]
        else:
            return None

    def __id_rank(self, student_id, scores):
        rank = 0.0
        for i in range(len(student_id)):
            rank += scores[i][int(student_id[i])]
        return rank

    def __update_student_id(self, new_id):
        if new_id is None or new_id == '-1':
            self.image.id = None
            self.student_id = '-1'
        else:
            self.image.id = new_id
            self.student_id = new_id
        self.image.clean_drawn_image()

    def __saved_image_name(self, filename_pattern):
        if self.student_id != '-1':
            sid = self.student_id
        else:
            sid = 'noid'
        filename = regexp_seqnum.sub(str(self.im_id), filename_pattern)
        filename = regexp_id.sub(sid, filename)
        return filename

class PerformanceProfiler(object):
    def __init__(self):
        self.start()
        self.num_captures = 0
        self.num_student_id_changes = 0

    def start(self):
        self.time0 = time.time()

    def count_capture(self):
        self.num_captures += 1

    def count_student_id_change(self):
        self.num_student_id_changes += 1

    def finish_exam(self, exam):
        time1 = time.time()
        stats = {}
        stats['time'] = time1 - self.time0
        stats['manual-changes'] = exam.num_manual_changes()
        stats['num-captures'] = self.num_captures
        stats['num-student-id-changes'] = self.num_student_id_changes
        self.compute_ocr_stats(stats, exam)
        self.time0 = time1
        self.num_captures = 0
        self.num_student_id_changes = 0
        return stats

    def compute_ocr_stats(self, stats, exam):
        if exam.image.id is None or exam.image.id_ocr_original is None:
            digits_total = 0
            digits_error = 0
        else:
            digits_total = len(exam.image.id)
            digits_error = len([1 for a, b in zip(exam.image.id,
                                                  exam.image.id_ocr_original) \
                                    if a != b])
        stats['id-ocr-digits-total'] = digits_total
        stats['id-ocr-digits-error'] = digits_error
        if exam.image.id_ocr_original is not None:
            stats['id-ocr-detected'] = exam.image.id_ocr_original
        else:
            stats['id-ocr-detected'] = '-1'

def read_cmd_options():
    parser = OptionParser(usage = "usage: %prog [options] EXAM_CONFIG_FILE",
                          version = utils.program_name + ' ' + utils.version)
    parser.add_option("-e", "--exam-data-file", dest = "ex_data_filename",
                      help = "read model data from FILENAME")
    parser.add_option("-a", "--answers-file", dest = "answers_filename",
                      help = "write students' answers to FILENAME")
    parser.add_option("-s", "--start-id", dest = "start_id", type = "int",
                      help = "start at the given exam id",
                      default = 0)
    parser.add_option("-o", "--output-dir", dest = "output_dir", default = '.',
                      help = "store captured images at the given directory")
    parser.add_option("-d", "--debug", action="store_true", dest = "debug",
                      default = False, help = "activate debugging features")
    parser.add_option("-c", "--camera", type="int", dest = "camera_dev",
                      help = "camera device to be selected (-1 for default)")
    parser.add_option("--stats", action="store_true", dest = "save_stats",
                      default = False,
                      help = "save performance stats to the answers file")
    parser.add_option("--id-list", dest = "ids_file", default = None,
                      help = "file with the list of valid student ids")
    parser.add_option("--capture-raw", dest = "raw_file", default = None,
                      help = "capture from raw file")
    parser.add_option("--capture-proc", dest = "proc_file", default = None,
                      help = "capture from pre-processed file")
    parser.add_option("--fixed-hough", dest = "fixed_hough", default = None,
                      type = "int", help = "fixed Hough transform threshold")
    parser.add_option("-f", "--ajust-first", action="store_true",
                      dest = "adjust", default = False,
                      help = "don't lock on an exam until SPC is pressed")

    (options, args) = parser.parse_args()
    if len(args) == 1:
        options.ex_data_filename = args[0]
    elif len(args) == 0:
        parser.error("Exam configuration file required")
    elif len(args) > 1:
        parser.error("Too many input command-line parameters")
    if options.raw_file is not None and options.proc_file is not None:
        parser.error("--capture-raw and --capture-proc are mutually exclusive")
    return options

def cell_clicked(image, point):
    min_dst = None
    clicked_row = None
    clicked_col = None
    for i, row in enumerate(image.centers):
        for j, center in enumerate(row):
            dst = imageproc.distance(point, center)
            if min_dst is None or dst < min_dst:
                min_dst = dst
                clicked_row = i
                clicked_col = j
    if min_dst is not None and min_dst <= image.diagonals[i][j] / 2:
        return (clicked_row, clicked_col + 1)
    else:
        return None

def dump_camera_buffer(camera):
    if camera is not None:
        for i in range(0, 6):
            imageproc.capture(camera, False)

def select_camera(options, config):
    if options.camera_dev is None:
        camera = config['camera-dev']
    else:
        camera = options.camera_dev
    return camera

def main():
    options = read_cmd_options()
    config = utils.read_config()
    save_pattern = config['save-filename-pattern']

    exam_data = utils.ExamConfig(options.ex_data_filename)
    solutions = exam_data.solutions
    dimensions = exam_data.dimensions
    id_num_digits = exam_data.id_num_digits
    read_id = (id_num_digits > 0)
    save_pattern = os.path.join(options.output_dir, save_pattern)
    if options.answers_filename is not None:
        answers_file = options.answers_filename
    else:
        answers_file = 'eyegrade-answers.csv'
        answers_file = os.path.join(options.output_dir, answers_file)

    im_id = options.start_id
    valid_student_ids = None
    if options.ids_file is not None:
        valid_student_ids = utils.read_student_ids(options.ids_file, True)

    interface = gui.PygameInterface((640, 480), read_id, options.ids_file)

    profiler = PerformanceProfiler()

    # Initialize options
    imageproc_options = imageproc.ExamCapture.get_default_options()
    imageproc_options['infobits'] = True
    imageproc_options['logging-dir'] = options.output_dir
    if read_id:
        imageproc_options['read-id'] = True
        imageproc_options['id-num-digits'] = id_num_digits
    if options.debug:
        imageproc_options['show-status'] = True
        imageproc_options['error-logging'] = True
    if config['error-logging']:
        imageproc_options['error-logging'] = True
    if not options.fixed_hough:
        imageproc_context = imageproc.ExamCaptureContext()
    else:
        imageproc_context = imageproc.ExamCaptureContext(options.fixed_hough)

    # Initialize capture source
    if options.proc_file is not None:
        imageproc_options['capture-from-file'] = True
        imageproc_options['capture-proc-file'] = options.proc_file
    elif options.raw_file is not None:
        imageproc_options['capture-from-file'] = True
        imageproc_options['capture-raw-file'] = options.raw_file
    else:
        imageproc_context.init_camera(select_camera(options, config))
        if imageproc_context.camera is None:
            print >> sys.stderr, 'ERROR: No camera found!'
            sys.exit(1)

    # Program main loop
    lock_mode = not options.adjust
    last_time = time.time()
    interface.update_text('Searching...', False)
    interface.set_statusbar_message(utils.program_name + ' ' + utils.version)
    interface.set_search_toolbar(True)
    latest_graded_exam = None
    while True:
        override_id_mode = False
        exam = None
        model = None
        manual_detection = False
        profiler.count_capture()
        image = imageproc.ExamCapture(dimensions, imageproc_context,
                                      imageproc_options)
        image.detect_safe()
        success = image.success
        if image.status['infobits']:
            model = utils.decode_model(image.bits)
            if model is not None and model in solutions:
                exam = Exam(image, model, solutions[model], valid_student_ids,
                            im_id, options.save_stats, exam_data.score_weights)
                exam.grade()
                latest_graded_exam = exam
            else:
                success = False

        events = interface.events_search_mode()
        for event, event_info in events:
            if event == gui.event_quit:
                sys.exit(0)
            elif event == gui.event_debug_proc and options.debug:
                imageproc_options['show-image-proc'] = \
                    not imageproc_options['show-image-proc']
            elif event == gui.event_debug_lines and options.debug:
                imageproc_options['show-lines'] = \
                    not imageproc_options['show-lines']
            elif event == gui.event_snapshot:
                if latest_graded_exam is None:
                    exam = Exam(image, model, {}, valid_student_ids, im_id,
                                options.save_stats, exam_data.score_weights)
                    exam.grade()
                else:
                    exam = latest_graded_exam
                success = True
                if options.ids_file is not None:
                    exam.reset_student_id_filter(False)
                else:
                    exam.reset_student_id_editor()
                    override_id_mode = True
            elif event == gui.event_lock:
                lock_mode = True
            elif event == gui.event_manual_detection:
                manual_detection = True
                exam = Exam(image, model, {}, valid_student_ids, im_id,
                            options.save_stats, exam_data.score_weights)

        # Enter manual detection mode. Users are expected here to
        # manually enter cell corners
        if manual_detection:
            points = []
            continue_waiting = True
            interface.show_capture(exam.image.image_drawn, True)
            while continue_waiting:
                event, event_info = interface.wait_event_review_mode()
                if event == gui.event_quit:
                    sys.exit(0)
                elif event == gui.event_click:
                    points.append(event_info)
                    exam.image.draw_corner(event_info)
                    print event_info
                    interface.show_capture(exam.image.image_drawn, True)
                    if len(points) == 4 * len(exam.image.boxes_dim):
                        imageproc.process_box_corners(points, exam.image.boxes_dim)
                        sys.exit(0)

        # Enter review mode if the capture was succesfully read or the
        # image was locked by the user
        if success and lock_mode:
            continue_waiting = True
            exam.lock_capture()
            exam.draw_answers()
            interface.show_capture(exam.image.image_drawn, False)
            interface.update_text(exam.get_student_name(), False)
            if exam.score is not None:
                interface.update_status(exam.score, False)
            interface.set_review_toolbar(True)
            while continue_waiting:
                event, event_info = interface.wait_event_review_mode()
                if event == gui.event_quit:
                    sys.exit(0)
                elif event == gui.event_cancel_frame:
                    continue_waiting = False
                elif event == gui.event_save:
                    stats = profiler.finish_exam(exam)
                    exam.save_image(save_pattern)
                    exam.save_answers(answers_file, config['csv-dialect'],
                                      stats)
                    if options.debug:
                        exam.save_debug_images(save_pattern)
                    im_id += 1
                    continue_waiting = False
                elif event == gui.event_manual_id:
                    override_id_mode = True
                    exam.reset_student_id_editor()
                    exam.draw_answers()
                    interface.show_capture(exam.image.image_drawn, False)
                    interface.update_text(exam.get_student_name())
                elif event == gui.event_next_id:
                    if not override_id_mode and options.ids_file is not None:
                        if len(exam.student_id_filter) == 0:
                            exam.try_next_student_id()
                        else:
                            exam.reset_student_id_filter()
                        profiler.count_student_id_change()
                        exam.draw_answers()
                        interface.show_capture(exam.image.image_drawn, False)
                        interface.update_text(exam.get_student_name())
                elif event == gui.event_id_digit:
                    if override_id_mode:
                        exam.student_id_editor(event_info)
                    elif options.ids_file is not None:
                        exam.filter_student_id(event_info)
                    profiler.count_student_id_change()
                    exam.draw_answers()
                    interface.show_capture(exam.image.image_drawn, False)
                    interface.update_text(exam.get_student_name())
                elif event == gui.event_debug_proc and options.debug:
                    imageproc_options['show-image-proc'] = \
                        not imageproc_options['show-image-proc']
                    continue_waiting = False
                elif event == gui.event_debug_lines and options.debug:
                    imageproc_options['show-lines'] = \
                        not imageproc_options['show-lines']
                    continue_waiting = False
                elif event == gui.event_click:
                    cell = cell_clicked(exam.image, event_info)
                    if cell is not None:
                        question, answer = cell
                        exam.toggle_answer(question, answer)
                        interface.show_capture(exam.image.image_drawn, False)
                        interface.update_status(exam.score)
            dump_camera_buffer(imageproc_context.camera)
            interface.update_text('Searching...', False)
            interface.update_status(None, False)
            interface.set_search_toolbar(True)
            latest_graded_exam = None
            if imageproc_options['capture-from-file']:
                sys.exit(0)
        else:
            if exam is not None:
                exam.draw_answers()
            else:
                image.draw_status()
            interface.show_capture(image.image_drawn)
            current_time = time.time()
            diff = current_time - last_time
            if current_time > last_time and diff < param_max_wait_time:
                interface.delay(param_max_wait_time - diff)
                last_time += 1
            else:
                if diff > 3 * param_max_wait_time:
                    dump_camera_buffer(imageproc_context.camera)
                last_time = current_time
            if imageproc_options['capture-from-file']:
                interface.wait_key()
                sys.exit(1)

if __name__ == "__main__":
    main()
