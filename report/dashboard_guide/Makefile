# Build the operator guide. Requires pdflatex (TinyTeX or TeX Live).
MAIN := main
.PHONY: all clean view
all:
	pdflatex -interaction=nonstopmode -halt-on-error $(MAIN).tex
	pdflatex -interaction=nonstopmode -halt-on-error $(MAIN).tex
	@echo "built $(MAIN).pdf"
clean:
	rm -f *.aux *.log *.out *.toc
view: all
	xdg-open $(MAIN).pdf
