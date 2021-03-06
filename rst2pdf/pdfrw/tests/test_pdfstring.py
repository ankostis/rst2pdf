'''
Run from the directory above like so:
python -m tests.test_pdfstring
'''


from rst2pdf import pdfrw
import unittest


class TestEncoding(unittest.TestCase):

    @staticmethod
    def decode(value):
        return pdfrw.PdfString(value).decode()

    @staticmethod
    def encode(value):
        return str(pdfrw.PdfString.encode(value))

    @classmethod
    def encode_decode(cls, value):
        return cls.decode(cls.encode(value))

    def roundtrip(self, value):
        self.assertEqual(value, self.encode_decode(value))

    def test_doubleslash(self):
        self.roundtrip('\\')


def main():
    unittest.main()


if __name__ == '__main__':
    main()
