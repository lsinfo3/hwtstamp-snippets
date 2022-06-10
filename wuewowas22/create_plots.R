library(tidyverse)

# settings
base_folder <- dirname(sys.frame(1)$ofile)
in_folder <- file.path(base_folder, "csvs")
out_folder <- file.path(base_folder)
setwd(out_folder)
print(paste("Data Input Folder:", in_folder))
print(paste("Plot Output Folder:", out_folder))

conf <- 0.95
plot_w <- 8.8
plot_h <- 2.0

# helper function
mutate_cond <- function(.data, condition, ..., envir = parent.frame()) {
  condition <- eval(substitute(condition), .data, envir)
  .data[condition, ] <- .data[condition, ] %>% mutate(...)
  .data
}

# plot theme stuff
myTheme <- function(...) {
  return(theme_light() + theme(
    strip.text.x = element_text(color = "gray20", size = 8, margin = margin(4, 0, 4, 0, "pt"))
    , strip.text.y = element_text(color = "gray20", size = 8, margin = margin(0, 4, 0, 4, "pt"))
    , strip.background = element_rect(color = "gray", fill = "gray95")
    , panel.grid.minor = element_line(color = "gray86", linetype = "dotted")
    , panel.grid.major = element_line(color = "gray82", linetype = "dotted")
    , legend.key = element_rect(fill = "gray95", colour = "gray85", size = 0.25)
    , legend.margin = margin(l = 0, r = 0, unit = "pt")
    , ...
  ))
}
myPalette <- function(numElements) {
  #colorRampPalette(c("#FFF5EB", "#FEE6CE", "#FDD0A2", "#FDAE6B", "#FD8D3C", "#F16913", "#D94801", "#A63603", "#7F2704"))(numElements)
  colorRampPalette(c("#FDD0A2", "#FDAE6B", "#FD8D3C", "#F16913", "#D94801", "#A63603", "#7F2704"))(numElements)
}

# read data frames
runs <- list(hard = 0, soft = 0, hard_wrong = 0)

# read all files and store them in a large data frame
df <- tibble()
for (file in list.files(path=in_folder, pattern="^.*.csv$")) {
  df_part <- read_delim(file.path(in_folder, file), delim = ";", col_types = cols(
      rep = col_integer(),
      type = col_character(),
      delay = col_integer(),
      time1 = col_integer(),
      time2 = col_integer()
    ))
  
  type <- df_part[1,]$type
  run <- runs[[type]] + 1
  runs[[type]] <- run
  
  df_part <- df_part %>% mutate(run=as.factor(run))
  df <- df %>% bind_rows(df_part)
}

# fix negative data
df2 <- df %>% mutate_cond(type == "hard" & delay < 0, delay = -delay) %>%
              mutate_cond(type == "hard_wrong" & delay < 0, delay = delay + 1e9)

# make results more pretty
df2 <- df2 %>% mutate_cond(type == "hard_wrong", delay = delay - 25e7) %>%
               filter(rep <= 150)

# rename types and change their order of plots
df2 <- df2 %>% mutate(type_fancy = factor(type, levels=c("soft", "hard_wrong", "hard")))
levels(df2$type_fancy) <- list("Software TS" = "soft", "Hardware TS, wrong ports" = "hard_wrong", "Hardware TS" = "hard")

# plot data
mapping <- aes(
    x = rep
    , y = delay / 1e3
    , color = run
)
plot <- (ggplot(df2, mapping)
        + facet_wrap(~ type_fancy, scales="free_y")
        + geom_line()
        #+ ggtitle("Hardware time stamp verification measurement")
        + xlab("Ping sequence ID")
        + ylab("Time stamp diff [µs]")
        + scale_color_discrete(name = "Run")
        + myTheme()
        )
ggsave("hwtstamp_measurements.pdf", plot=plot, width=plot_w, height=plot_h)
